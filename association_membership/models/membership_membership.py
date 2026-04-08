from collections import Counter
from datetime import date

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


MEMBERSHIP_STATE_SELECTION = [
    ("draft", "Draft"),
    ("waiting", "Waiting"),
    ("active", "Active"),
    ("cancelled", "Cancelled"),
    ("terminated", "Terminated"),
]

BUSINESS_ACTIVE_STATES = ("active", "cancelled")


class MembershipMembership(models.Model):
    _name = "membership.membership"
    _description = "Membership"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_name", store=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Member",
        required=True,
        tracking=True,
        index=True,
    )
    invoice_partner_id = fields.Many2one(
        "res.partner",
        string="Invoice Contact",
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Membership Product",
        required=True,
        tracking=True,
        index=True,
    )
    state = fields.Selection(
        selection=MEMBERSHIP_STATE_SELECTION,
        required=True,
        default="draft",
        tracking=True,
        index=True,
        group_expand="_read_group_state",
    )
    date_start = fields.Date(
        required=True,
        tracking=True,
        index=True,
        default=lambda self: fields.Date.context_today(self),
    )
    date_end = fields.Date(tracking=True, index=True)
    date_cancelled = fields.Date(tracking=True, index=True)
    cancel_reason = fields.Text(tracking=True)
    date_welcome_sent = fields.Date(
        tracking=True,
        copy=False,
    )
    membership_active = fields.Boolean(
        string="Membership Active",
        compute="_compute_membership_active",
        store=True,
        index=True,
    )
    membership_number = fields.Char(
        tracking=True,
        index=True,
        copy=False,
    )
    override_membership_number = fields.Boolean(
        string="Override",
        copy=False,
    )
    membership_number_preview = fields.Char(
        compute="_compute_membership_number_preview",
        string="Membership Number (Preview)",
    )
    amount_override = fields.Monetary(tracking=True)
    has_amount_override = fields.Boolean(copy=False)
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    contribution_ids = fields.One2many(
        "membership.contribution",
        "membership_id",
        string="Contributions",
    )
    duplicate_contribution_year_warning = fields.Char(
        compute="_compute_duplicate_contribution_year_warning",
    )
    contribution_count = fields.Integer(compute="_compute_contribution_count")
    last_contribution_year = fields.Integer(
        compute="_compute_last_contribution_data",
        store=True,
    )
    last_billing_status = fields.Char(
        compute="_compute_last_contribution_data",
        store=True,
    )
    active = fields.Boolean(default=True)
    membership_category_id = fields.Many2one(
        "product.category",
        compute="_compute_membership_category_id",
    )
    partner_avatar_128 = fields.Image(
        related="partner_id.avatar_128",
        readonly=True,
    )

    _sql_constraints = [
        (
            "membership_member_number_uniq",
            "unique(membership_number)",
            "The membership number must be globally unique.",
        ),
    ]

    def _auto_init(self):
        result = super()._auto_init()
        today = date.today()
        self.env.cr.execute(
            """
            UPDATE membership_membership
               SET state = CASE
                   WHEN date_end > %s THEN 'cancelled'
                   ELSE 'terminated'
               END
             WHERE state = 'active'
               AND date_end IS NOT NULL
            """,
            (today,),
        )
        self._migrate_legacy_membership_numbers()
        return result

    def _migrate_legacy_membership_numbers(self):
        self.env.cr.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'membership_membership'
               AND column_name IN ('membership_number', 'member_number', 'external_ref')
            """
        )
        available_columns = {row[0] for row in self.env.cr.fetchall()}
        if "membership_number" not in available_columns:
            return

        if "member_number" in available_columns:
            self.env.cr.execute(
                """
                UPDATE membership_membership
                   SET membership_number = NULLIF(BTRIM(member_number), '')
                 WHERE (membership_number IS NULL OR BTRIM(membership_number) = '')
                   AND member_number IS NOT NULL
                   AND BTRIM(member_number) != ''
                """
            )

        if "external_ref" not in available_columns:
            return

        self.env.cr.execute(
            """
            SELECT id,
                   NULLIF(BTRIM(membership_number), '') AS membership_number,
                   NULLIF(BTRIM(external_ref), '') AS external_ref
              FROM membership_membership
            """
        )
        rows = self.env.cr.dictfetchall()
        existing_numbers = {}
        pending_numbers = {}
        for row in rows:
            if row["membership_number"]:
                existing_numbers.setdefault(row["membership_number"], []).append(row["id"])
            elif row["external_ref"]:
                pending_numbers.setdefault(row["external_ref"], []).append(row["id"])

        duplicate_number = next(
            (number for number, ids in pending_numbers.items() if len(ids) > 1),
            False,
        )
        if duplicate_number:
            raise ValidationError(
                _(
                    "Cannot migrate legacy external references because '%s' is used on multiple memberships."
                )
                % duplicate_number
            )

        conflicting_number = next(
            (number for number in pending_numbers if number in existing_numbers),
            False,
        )
        if conflicting_number:
            raise ValidationError(
                _(
                    "Cannot migrate legacy external references because '%s' is already used as a membership number."
                )
                % conflicting_number
            )

        self.env.cr.execute(
            """
            UPDATE membership_membership
               SET membership_number = NULLIF(BTRIM(external_ref), '')
             WHERE (membership_number IS NULL OR BTRIM(membership_number) = '')
               AND external_ref IS NOT NULL
               AND BTRIM(external_ref) != ''
            """
        )

    @api.depends("partner_id", "product_id", "company_id")
    def _compute_name(self):
        for record in self:
            parts = [
                record.partner_id.display_name,
                record.product_id.display_name,
                record.company_id.display_name,
            ]
            record.name = " - ".join(part for part in parts if part)

    @api.depends("membership_number", "company_id")
    def _compute_membership_number_preview(self):
        sequence = self.env["ir.sequence"].sudo().search(
            [("code", "=", "association.membership.number.seq")],
            limit=1,
        )
        next_counter = str(sequence.number_next_actual) if sequence else False
        for record in self:
            if record.membership_number:
                record.membership_number_preview = record.membership_number
                continue
            if not next_counter:
                record.membership_number_preview = False
                continue
            prefix = record.company_id._render_member_number_prefix(
                target_date=fields.Date.context_today(record)
            )
            record.membership_number_preview = "%s%s" % (
                prefix,
                next_counter.zfill(record.company_id.member_number_padding),
            )

    @api.depends("state")
    def _compute_membership_active(self):
        for record in self:
            record.membership_active = record.state in BUSINESS_ACTIVE_STATES

    @api.depends("contribution_ids.membership_year")
    def _compute_duplicate_contribution_year_warning(self):
        for record in self:
            duplicate_years = record._get_duplicate_contribution_years()
            record.duplicate_contribution_year_warning = (
                _("More than one contribution exists for year(s): %s.")
                % ", ".join(str(year) for year in duplicate_years)
                if duplicate_years
                else False
            )

    @api.depends("contribution_ids")
    def _compute_contribution_count(self):
        for record in self:
            record.contribution_count = len(record.contribution_ids)

    @api.depends("contribution_ids.membership_year", "contribution_ids.billing_status")
    def _compute_last_contribution_data(self):
        for record in self:
            contributions = record.contribution_ids.sorted(
                key=lambda contribution: (contribution.membership_year, contribution.id)
            )
            latest = contributions[-1:] if contributions else self.env["membership.contribution"]
            record.last_contribution_year = latest.membership_year if latest else 0
            record.last_billing_status = latest.billing_status if latest else False

    @api.depends("company_id")
    def _compute_membership_category_id(self):
        for record in self:
            record.membership_category_id = self._get_membership_category(company=record.company_id)

    @api.model
    def _get_membership_category(self, company=False):
        company = company or self.env.company
        return company._membership_product_category()

    @api.model
    def _is_auto_activate_on_payment_enabled(self, company=False):
        company = company or self.env.company
        return bool(company.membership_auto_activate_on_payment)

    def _get_duplicate_contribution_years(self):
        self.ensure_one()
        contribution_years = [
            year
            for year in self.contribution_ids.mapped("membership_year")
            if year
        ]
        duplicates = [
            year for year, count in Counter(contribution_years).items() if count > 1
        ]
        return sorted(duplicates)

    @api.onchange("contribution_ids", "contribution_ids.membership_year")
    def _onchange_contribution_ids_warning(self):
        duplicate_years = self._get_duplicate_contribution_years()
        if not duplicate_years:
            return {}
        return {
            "warning": {
                "title": _("Duplicate Contribution Year"),
                "message": _("More than one contribution exists for year(s): %s.")
                % ", ".join(str(year) for year in duplicate_years),
            }
        }

    @api.model
    def _read_group_state(self, values, domain):
        return [value for value, _label in MEMBERSHIP_STATE_SELECTION]

    @api.model
    def _resolve_default_invoice_partner(self, partner):
        if not partner:
            return self.env["res.partner"]
        invoice_partner_id = partner.address_get(["invoice"]).get("invoice")
        return self.env["res.partner"].browse(invoice_partner_id) or partner

    @api.model
    def _normalize_state_value(self, value):
        return value

    @api.model
    def _prepare_membership_values(
        self,
        vals,
        for_create=False,
        apply_invoice_partner_default=False,
    ):
        vals = vals.copy()
        if apply_invoice_partner_default and vals.get("partner_id") and not vals.get(
            "invoice_partner_id"
        ):
            partner = self.env["res.partner"].browse(vals["partner_id"])
            vals["invoice_partner_id"] = self._resolve_default_invoice_partner(partner).id
        if for_create:
            vals.setdefault("company_id", self.env.company.id)
            vals.setdefault("date_start", fields.Date.context_today(self))
        if "state" in vals:
            vals["state"] = self._normalize_state_value(vals["state"])
        if "membership_number" in vals:
            vals["membership_number"] = self._normalize_membership_number_value(
                vals["membership_number"]
            )
            if not self.env.context.get("skip_membership_number_override_flag"):
                vals.setdefault("override_membership_number", bool(vals["membership_number"]))
        if "amount_override" in vals:
            vals["has_amount_override"] = self._has_explicit_amount_override_value(
                vals["amount_override"]
            )
        if vals.get("state") in {"cancelled", "terminated"}:
            cancel_defaults = self._build_cancel_values(
                cancel_date=vals.get("date_cancelled"),
                cancel_reason=vals.get("cancel_reason"),
            )
            cancel_defaults.update(
                {
                    key: value
                    for key, value in vals.items()
                    if key in {"date_cancelled", "date_end", "cancel_reason"}
                }
            )
            vals.update(cancel_defaults)
        return vals

    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if not self.partner_id:
            return
        default_invoice_partner = self._resolve_default_invoice_partner(self.partner_id)
        if not self.invoice_partner_id or self.invoice_partner_id == self._origin.partner_id:
            self.invoice_partner_id = default_invoice_partner

    @api.onchange("product_id")
    def _onchange_product_id(self):
        if not self.product_id:
            self.amount_override = 0.0
            self.has_amount_override = False
            return
        self.amount_override = self._get_product_amount(self.product_id)
        self.has_amount_override = True

    @api.model
    def _membership_product_domain(self, company=False):
        company = company or self.env.company
        category = self._get_membership_category(company=company)
        if not category:
            return [("id", "=", 0)]
        return [
            ("active", "=", True),
            ("categ_id", "child_of", category.id),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", company.id),
        ]

    @api.onchange("company_id")
    def _onchange_company_id(self):
        company = self.company_id or self.env.company
        domain = self._membership_product_domain(company=company)
        if self.product_id and not self.env["product.product"].search_count(
            domain + [("id", "=", self.product_id.id)]
        ):
            self.product_id = False
        return {"domain": {"product_id": domain}}

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for record in self:
            if record.date_start and record.date_end and record.date_end < record.date_start:
                raise ValidationError(_("The end date cannot be before the start date."))

    @api.constrains("product_id")
    def _check_membership_product(self):
        for record in self:
            category = self._get_membership_category(company=record.company_id)
            if not category or not record.product_id:
                continue
            if not self.env["product.product"].search_count(
                [("id", "=", record.product_id.id), ("categ_id", "child_of", category.id)]
            ):
                raise ValidationError(
                    _(
                        "Only products from the configured Membership category can be used."
                    )
                )

    def _raise_membership_number_conflict(self, number, conflict):
        raise ValidationError(
            _("Membership Number '%(number)s' is already assigned to %(membership)s.")
            % {
                "number": number,
                "membership": conflict.display_name,
            }
        )

    @api.constrains("membership_number")
    def _check_membership_number_unique(self):
        for record in self.filtered("membership_number"):
            conflict = self.sudo().with_context(active_test=False).search(
                [
                    ("id", "!=", record.id),
                    ("membership_number", "=", record.membership_number),
                ],
                limit=1,
            )
            if conflict:
                record._raise_membership_number_conflict(record.membership_number, conflict)

    @api.constrains("partner_id", "company_id", "product_id", "date_start", "date_end")
    def _check_date_overlap(self):
        for record in self:
            if not all([record.partner_id, record.company_id, record.product_id, record.date_start]):
                continue
            overlap_domain = [
                ("id", "!=", record.id),
                ("partner_id", "=", record.partner_id.id),
                ("company_id", "=", record.company_id.id),
                ("product_id", "=", record.product_id.id),
                ("date_start", "<=", record.date_end or date.max),
                "|",
                ("date_end", "=", False),
                ("date_end", ">=", record.date_start),
            ]
            if self.with_context(active_test=False).search_count(overlap_domain):
                raise ValidationError(
                    _(
                        "There is already a membership for this member, company, and product with overlapping dates."
                    )
                )

    @api.model
    def _build_cancel_values(self, cancel_date=False, cancel_reason=False):
        today = cancel_date or fields.Date.context_today(self)
        cancel_year = fields.Date.to_date(today).year
        return {
            "date_cancelled": today,
            "date_end": date(cancel_year, 12, 31),
            "cancel_reason": cancel_reason or False,
        }

    @api.model
    def _has_explicit_amount_override_value(self, value):
        return value is not False and value is not None and value != ""

    @api.model
    def _get_product_amount(self, product):
        if not product:
            return 0.0
        if "lst_price" in product._fields and product.lst_price not in (False, None):
            return product.lst_price
        if "list_price" in product._fields and product.list_price not in (False, None):
            return product.list_price
        template = product.product_tmpl_id if "product_tmpl_id" in product._fields else self.env["product.template"]
        if template and "list_price" in template._fields and template.list_price not in (False, None):
            return template.list_price
        return 0.0

    @api.model
    def _normalize_membership_number_value(self, value):
        if value in (False, None):
            return False
        normalized = str(value).strip()
        return normalized or False

    @api.model
    def _check_explicit_membership_number_conflicts(self, vals_list):
        explicit_numbers = [
            vals["membership_number"]
            for vals in vals_list
            if vals.get("membership_number")
        ]
        if not explicit_numbers:
            return
        duplicates_in_batch = [
            number for number, count in Counter(explicit_numbers).items() if count > 1
        ]
        if duplicates_in_batch:
            duplicate = duplicates_in_batch[0]
            conflict = self.new({"membership_number": duplicate, "name": duplicate})
            self._raise_membership_number_conflict(duplicate, conflict)
        conflicts = self.sudo().with_context(active_test=False).search(
            [("membership_number", "in", explicit_numbers)]
        )
        conflicts_by_number = {
            membership.membership_number: membership for membership in conflicts
        }
        for number in explicit_numbers:
            conflict = conflicts_by_number.get(number)
            if conflict:
                self._raise_membership_number_conflict(number, conflict)

    def _next_membership_number_counter(self):
        counter = self.env["ir.sequence"].sudo().next_by_code("association.membership.number.seq")
        if not counter:
            raise UserError(_("The membership number counter is not configured."))
        return str(counter)

    def _generate_membership_number(self):
        self.ensure_one()
        counter = self._next_membership_number_counter()
        prefix = self.company_id._render_member_number_prefix(
            target_date=fields.Date.context_today(self)
        )
        return "%s%s" % (prefix, counter.zfill(self.company_id.member_number_padding))

    def _assign_membership_number_if_missing(self):
        for record in self.filtered(lambda membership: not membership.membership_number):
            record.with_context(skip_membership_number_override_flag=True).write(
                {
                    "membership_number": record._generate_membership_number(),
                    "override_membership_number": False,
                }
            )

    def _default_contribution_year(self):
        self.ensure_one()
        return self.company_id.membership_default_contribution_year or fields.Date.context_today(self).year

    def _prepare_contribution_create_values(self, membership_year=False, **overrides):
        self.ensure_one()
        vals = {"membership_id": self.id}
        if membership_year not in (False, None, ""):
            vals["membership_year"] = membership_year
        vals.update(overrides)
        return self.env["membership.contribution"]._prepare_membership_contribution_values(
            vals,
            membership=self,
        )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = [
            self._prepare_membership_values(
                vals,
                for_create=True,
                apply_invoice_partner_default=True,
            )
            for vals in vals_list
        ]
        self._check_explicit_membership_number_conflicts(prepared_vals_list)
        try:
            records = super().create(prepared_vals_list)
            records._assign_membership_number_if_missing()
        except IntegrityError as exc:
            constraint_name = getattr(getattr(exc, "diag", None), "constraint_name", "")
            if constraint_name in {
                "membership_member_number_uniq",
                "membership_membership_membership_member_number_uniq",
                "membership_membership_membership_number_uniq",
            }:
                raise ValidationError(_("The membership number must be globally unique.")) from exc
            raise
        records._sync_optional_partner_relations()
        return records

    def write(self, vals):
        if "state" in vals and not self.env.context.get("allow_membership_state_write"):
            raise UserError(_("Use the membership actions instead of writing the state directly."))
        vals = self._prepare_membership_values(vals)
        result = super().write(vals)
        if {
            "partner_id",
            "invoice_partner_id",
            "company_id",
            "product_id",
            "state",
            "date_start",
            "date_end",
            "date_cancelled",
        } & set(vals):
            self._sync_optional_partner_relations()
        return result

    def _get_allowed_transitions(self):
        return {
            "draft": {"waiting"},
            "waiting": {"draft", "active"},
            "active": {"cancelled", "terminated"},
            "cancelled": {"active", "terminated"},
            "terminated": {"waiting"},
        }

    def _get_invoice_partner(self):
        self.ensure_one()
        return self.invoice_partner_id or self._resolve_default_invoice_partner(self.partner_id)

    def _get_default_cancel_values(self, cancel_date=False, cancel_reason=False):
        self.ensure_one()
        return self._build_cancel_values(
            cancel_date=cancel_date,
            cancel_reason=cancel_reason,
        )

    def _schedule_termination(self, **kwargs):
        today = date.today()
        for record in self:
            vals = record._get_default_cancel_values(
                cancel_date=kwargs.get("date_cancelled"),
                cancel_reason=kwargs.get("cancel_reason"),
            )
            if kwargs.get("date_end"):
                vals["date_end"] = kwargs["date_end"]
            if vals.get("date_end") and vals["date_end"] <= today:
                record._do_transition(
                    "terminated",
                    date_cancelled=vals.get("date_cancelled"),
                    date_end=vals.get("date_end"),
                    cancel_reason=vals.get("cancel_reason"),
                )
                continue
            record._do_transition(
                "cancelled",
                date_cancelled=vals.get("date_cancelled"),
                date_end=vals.get("date_end"),
                cancel_reason=vals.get("cancel_reason"),
            )
        return True

    def _do_transition(self, new_state, **kwargs):
        allowed = self._get_allowed_transitions()
        new_state = self._normalize_state_value(new_state)
        for record in self:
            if new_state == record.state:
                continue
            if new_state not in allowed.get(record.state, set()):
                raise UserError(
                    _(
                        "You cannot move a membership from %(from_state)s to %(to_state)s."
                    )
                    % {
                        "from_state": record.state,
                        "to_state": new_state,
                    }
                )
            vals = {"state": new_state}
            if new_state in {"cancelled", "terminated"}:
                vals.update(
                    record._get_default_cancel_values(
                        cancel_date=kwargs.get("date_cancelled"),
                        cancel_reason=kwargs.get("cancel_reason"),
                    )
                )
                if kwargs.get("date_end"):
                    vals["date_end"] = kwargs["date_end"]
            elif record.state == "cancelled" and new_state == "active":
                vals.update(
                    {
                        "date_cancelled": False,
                        "date_end": False,
                        "cancel_reason": False,
                    }
                )
            elif record.state == "terminated" and new_state == "waiting":
                vals.update(
                    {
                        "date_cancelled": kwargs.get("date_cancelled", False),
                        "date_end": kwargs.get("date_end", False),
                        "cancel_reason": kwargs.get("cancel_reason", False),
                    }
                )
            record.with_context(allow_membership_state_write=True).write(vals)
        return True

    def action_submit(self):
        self._do_transition("waiting")
        return True

    def action_activate(self):
        self._do_transition("active")
        return True

    def action_activate_from_payment(self, invoice=False):
        waiting_memberships = self.filtered(lambda membership: membership.state == "waiting")
        if not waiting_memberships:
            return True
        waiting_memberships._do_transition("active")
        if invoice:
            for membership in waiting_memberships:
                membership.message_post(
                    body=_(
                        "Membership activated automatically after payment of invoice %s."
                    )
                    % invoice.display_name
                )
        return True

    def action_revert_to_draft(self):
        self._do_transition("draft")
        return True

    def action_reopen_waiting(self):
        self._do_transition("waiting")
        return True

    def action_cancel(self):
        self.ensure_one()
        if self.state != "active":
            raise UserError(_("Only active memberships can be cancelled."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Cancel Membership"),
            "res_model": "membership.cancel.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_membership_id": self.id,
            },
        }

    def action_view_contributions(self):
        self.ensure_one()
        action = self.env.ref(
            "association_membership.action_membership_contribution"
        ).read()[0]
        action["domain"] = [("membership_id", "=", self.id)]
        action["context"] = {"default_membership_id": self.id}
        return action

    def action_view_invoices(self):
        self.ensure_one()
        invoice_ids = (
            self.contribution_ids.mapped("invoice_id")
            | self.contribution_ids.mapped("refund_move_id")
        ).ids
        return {
            "type": "ir.actions.act_window",
            "name": _("Membership Invoices"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", invoice_ids)],
            "context": {"create": False},
        }

    def _render_mail_template_field(self, template, field_name):
        self.ensure_one()
        if not template:
            return False
        lang = template._render_lang([self.id]).get(self.id)
        options = {"post_process": True} if field_name == "body_html" else {}
        return template.with_context(lang=lang)._render_field(
            field_name,
            [self.id],
            options=options,
        ).get(self.id)

    def action_create_contribution(self):
        self.ensure_one()
        contribution_year = self._default_contribution_year()
        contribution = self.env["membership.contribution"].search(
            [
                ("membership_id", "=", self.id),
                ("membership_year", "=", contribution_year),
            ],
            limit=1,
        )
        if not contribution:
            contribution = self.env["membership.contribution"].create(
                self._prepare_contribution_create_values(
                    membership_year=contribution_year,
                )
            )
            contribution._apply_invoicing_strategy(
                strategy=self.company_id.membership_invoicing_strategy,
                invoice_date=fields.Date.context_today(self),
            )
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def _resolve_amount_expected(self):
        self.ensure_one()
        if self.has_amount_override or self.amount_override not in (False, None):
            return self.amount_override or 0.0
        return self._get_product_amount(self.product_id)

    def _resolve_is_free(self, amount_value=False):
        self.ensure_one()
        if amount_value not in (False, None, ""):
            return float(amount_value or 0.0) == 0.0
        return float(self._resolve_amount_expected() or 0.0) == 0.0

    @api.model
    def _cron_target_year(self, company=False):
        company = company or self.env.company
        return company._membership_cron_target_year()

    @api.model
    def cron_generate_membership_renewals(self):
        companies = self.env["res.company"].search([])
        for company in companies:
            wizard = self.env["membership.renewal.wizard"].with_company(company).create(
                {
                    "target_year": self._cron_target_year(company=company),
                    "company_ids": [(6, 0, [company.id])],
                    "dry_run": False,
                }
            )
            wizard.action_run()
        return True

    @api.model
    def cron_terminate_expired_memberships(self):
        today = date.today()
        memberships = self.search(
            [
                ("state", "=", "cancelled"),
                ("date_end", "!=", False),
                ("date_end", "<", today),
            ]
        )
        for membership in memberships:
            membership._do_transition(
                "terminated",
                date_cancelled=membership.date_cancelled or membership.date_end,
                date_end=membership.date_end,
                cancel_reason=membership.cancel_reason,
            )
        return True

    def _sync_optional_partner_relations(self):
        if "res.partner.relation" not in self.env or "res.partner.relation.type" not in self.env:
            return
        relation_type = self._get_membership_relation_type()
        relation_model = self.env["res.partner.relation"]
        for record in self:
            company_partner = record.company_id.partner_id
            if not record.partner_id or not company_partner or not relation_type:
                continue
            relation = relation_model.search(
                [
                    ("left_partner_id", "=", record.partner_id.id),
                    ("right_partner_id", "=", company_partner.id),
                    ("type_id", "=", relation_type.id),
                ],
                limit=1,
            )
            if record.state in BUSINESS_ACTIVE_STATES:
                values = {
                    "left_partner_id": record.partner_id.id,
                    "right_partner_id": company_partner.id,
                    "type_id": relation_type.id,
                    "date_start": record.date_start,
                    "date_end": False,
                }
                if relation:
                    relation.write(values)
                else:
                    relation_model.create(values)
            elif record.state == "terminated" and relation:
                sibling_membership = self.search(
                    [
                        ("id", "!=", record.id),
                        ("partner_id", "=", record.partner_id.id),
                        ("company_id", "=", record.company_id.id),
                        ("state", "in", BUSINESS_ACTIVE_STATES),
                        "|",
                        ("date_end", "=", False),
                        ("date_end", ">=", record.date_end or fields.Date.context_today(record)),
                    ],
                    limit=1,
                )
                if not sibling_membership:
                    relation.write(
                        {
                            "date_end": record.date_end or fields.Date.context_today(record)
                        }
                    )

    @api.model
    def _get_membership_relation_type(self):
        if "res.partner.relation.type" not in self.env:
            return self.env["res.partner.relation.type"]
        relation_type = self.env["res.partner.relation.type"].search(
            [("name", "=", "Member Of"), ("name_inverse", "=", "Has Member")],
            limit=1,
        )
        if relation_type:
            return relation_type
        return self.env["res.partner.relation.type"].create(
            {
                "name": "Member Of",
                "name_inverse": "Has Member",
                "allow_self": False,
                "is_symmetric": False,
                "handle_invalid_onchange": "restrict",
            }
        )
