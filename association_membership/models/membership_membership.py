from collections import Counter
from datetime import date

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .res_company import INVOICING_STRATEGY_SELECTION


MEMBERSHIP_STATE_SELECTION = [
    ("draft", "Draft"),
    ("waiting", "Waiting"),
    ("active", "Active"),
    ("cancelled", "Cancelled"),
    ("terminated", "Terminated"),
]

# Business-active: the membership is live today. `cancelled` is "scheduled to
# end at date_end", so it still counts.
BUSINESS_ACTIVE_STATES = ("active", "cancelled")
# Everything an association would call "a member on the books", including the
# ones not activated yet. The single definition used by reports, the partner
# number display and the partner filters.
CURRENT_MEMBER_STATES = ("waiting", "active", "cancelled")


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
    # Template = membership type, variant = tier.
    product_tmpl_id = fields.Many2one(
        related="product_id.product_tmpl_id",
        string="Membership Type",
        store=True,
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
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    amount = fields.Monetary(
        compute="_compute_amount",
        store=True,
        readonly=False,
        tracking=True,
    )
    invoicing_strategy = fields.Selection(
        selection=INVOICING_STRATEGY_SELECTION,
        string="Invoicing Strategy",
        tracking=True,
        help="Leave empty to follow the company setting. Each period keeps"
             " the strategy that applied when it was created.",
    )
    period_ids = fields.One2many(
        "membership.period",
        "membership_id",
        string="Periods",
    )
    duplicate_period_year_warning = fields.Char(
        compute="_compute_duplicate_period_year_warning",
    )
    period_count = fields.Integer(compute="_compute_period_count")
    last_period_year = fields.Integer(
        compute="_compute_last_period_data",
        store=True,
    )
    last_billing_status = fields.Char(
        compute="_compute_last_period_data",
        store=True,
    )
    active = fields.Boolean(default=True)
    product_domain = fields.Binary(compute="_compute_product_domain")
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
        sequence_by_company = {}
        for record in self:
            if record.membership_number:
                record.membership_number_preview = record.membership_number
                continue
            company_id = record.company_id.id
            if company_id not in sequence_by_company:
                sequence_by_company[company_id] = record.company_id._get_membership_number_sequence()
            sequence = sequence_by_company[company_id]
            next_counter = str(sequence.number_next_actual) if sequence else False
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

    @api.depends("product_id")
    def _compute_amount(self):
        for record in self:
            record.amount = record.product_id._get_membership_price(record.company_id)

    @api.depends("period_ids.membership_year")
    def _compute_duplicate_period_year_warning(self):
        for record in self:
            duplicate_years = record._get_duplicate_period_years()
            record.duplicate_period_year_warning = (
                _("More than one period exists for year(s): %s.")
                % ", ".join(str(year) for year in duplicate_years)
                if duplicate_years
                else False
            )

    @api.depends("period_ids")
    def _compute_period_count(self):
        for record in self:
            record.period_count = len(record.period_ids)

    @api.depends("period_ids.membership_year", "period_ids.billing_status")
    def _compute_last_period_data(self):
        for record in self:
            periods = record.period_ids.sorted(
                key=lambda period: (period.membership_year, period.id)
            )
            latest = periods[-1:] if periods else self.env["membership.period"]
            record.last_period_year = latest.membership_year if latest else 0
            record.last_billing_status = latest.billing_status if latest else False

    @api.depends("company_id", "partner_id.is_company")
    def _compute_product_domain(self):
        for record in self:
            record.product_domain = record._membership_product_domain()

    def _get_invoicing_strategy(self):
        """The strategy that applies to this membership: own override, else company."""
        self.ensure_one()
        return self.invoicing_strategy or self.company_id.membership_invoicing_strategy

    def _get_duplicate_period_years(self):
        self.ensure_one()
        period_years = [
            year
            for year in self.period_ids.mapped("membership_year")
            if year
        ]
        duplicates = [
            year for year, count in Counter(period_years).items() if count > 1
        ]
        return sorted(duplicates)

    @api.onchange("period_ids", "period_ids.membership_year")
    def _onchange_period_ids_warning(self):
        duplicate_years = self._get_duplicate_period_years()
        if not duplicate_years:
            return {}
        return {
            "warning": {
                "title": _("Duplicate Period Year"),
                "message": _("More than one period exists for year(s): %s.")
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
    def _prepare_membership_values(
        self,
        vals,
        for_create=False,
        apply_invoice_partner_default=False,
    ):
        vals = vals.copy()
        if apply_invoice_partner_default and vals.get("partner_id"):
            partner = self.env["res.partner"].browse(vals["partner_id"])
            if not vals.get("invoice_partner_id"):
                vals["invoice_partner_id"] = self._resolve_default_invoice_partner(partner).id
        if for_create:
            vals.setdefault("company_id", self.env.company.id)
            vals.setdefault("date_start", fields.Date.context_today(self))
        if "membership_number" in vals:
            vals["membership_number"] = self._normalize_membership_number_value(
                vals["membership_number"]
            )
            if not self.env.context.get("skip_membership_number_override_flag"):
                vals.setdefault("override_membership_number", bool(vals["membership_number"]))
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
        self.invoice_partner_id = self._resolve_default_invoice_partner(self.partner_id)

    def _membership_product_domain(self):
        return self.env["product.product"]._membership_product_domain(
            self.company_id or self.env.company, self.partner_id
        )

    @api.onchange("company_id", "partner_id")
    def _onchange_membership_product(self):
        if self.product_id and not self.product_id.filtered_domain(self._membership_product_domain()):
            self.product_id = False

    @api.constrains("date_start", "date_end")
    def _check_dates(self):
        for record in self:
            if record.date_start and record.date_end and record.date_end < record.date_start:
                raise ValidationError(_("The end date cannot be before the start date."))

    @api.constrains("state", "date_cancelled", "date_end", "cancel_reason")
    def _check_cancel_fields_state(self):
        for record in self:
            if record.state in ("cancelled", "terminated"):
                continue
            if record.date_cancelled or record.date_end or record.cancel_reason:
                raise ValidationError(
                    _(
                        "The cancellation date, end date, and cancellation reason can"
                        " only be set on cancelled or terminated memberships."
                    )
                )

    @api.constrains("product_id", "partner_id", "company_id")
    def _check_membership_product(self):
        for record in self:
            if not record.product_id.filtered_domain(record._membership_product_domain()):
                raise ValidationError(
                    _(
                        "%(product)s cannot be used for this membership. Use a membership"
                        " product of %(company)s (or without company) that is meant"
                        " for %(partner_type)s."
                    )
                    % {
                        "product": record.product_id.display_name,
                        "company": record.company_id.display_name,
                        "partner_type": _("organisations")
                        if record.partner_id.is_company
                        else _("individuals"),
                    }
                )

    @api.constrains("product_id")
    def _check_membership_type_unchanged(self):
        for record in self:
            period_types = record.period_ids.product_id.product_tmpl_id
            if period_types - record.product_tmpl_id:
                raise ValidationError(
                    _(
                        "The membership type cannot be changed once periods exist."
                        " Only a tier of the same type can be selected. To change the type,"
                        " end this membership and start a new one."
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
                ("product_tmpl_id", "=", record.product_tmpl_id.id),
                ("date_start", "<=", record.date_end or date.max),
                "|",
                ("date_end", "=", False),
                ("date_end", ">=", record.date_start),
            ]
            if self.with_context(active_test=False).search_count(overlap_domain):
                raise ValidationError(
                    _(
                        "There is already a membership of this type for this member and company"
                        " with overlapping dates."
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
        sequence = self.company_id._get_membership_number_sequence()
        counter = sequence.sudo().next_by_id()
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

    def _default_period_year(self):
        self.ensure_one()
        return self.company_id._membership_period_year()

    def _prepare_period_create_values(self, membership_year=False, **overrides):
        self.ensure_one()
        vals = {"membership_id": self.id}
        if membership_year not in (False, None, ""):
            vals["membership_year"] = membership_year
        vals.update(overrides)
        return self.env["membership.period"]._prepare_membership_period_values(
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
            # The creator does not follow: followers get copies of member emails (5.3).
            records = super(
                MembershipMembership, self.with_context(mail_create_nosubscribe=True)
            ).create(prepared_vals_list)
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

    def unlink(self):
        for record in self:
            if record.state != "draft":
                raise UserError(
                    _(
                        "Only memberships in Draft state can be deleted. "
                        "Cancel or terminate the membership instead."
                    )
                )
            record._check_no_periods()
        return super().unlink()

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
            "waiting": {"draft", "active", "cancelled", "terminated"},
            "active": {"cancelled", "terminated", "draft"},
            "cancelled": {"waiting", "active", "terminated", "draft"},
            "terminated": {"waiting", "draft"},
        }

    def _check_no_periods(self):
        for record in self:
            if record.period_ids:
                raise UserError(
                    _(
                        "Membership %s has periods, so it cannot be deleted or"
                        " reverted to draft. Cancel or terminate it instead."
                    )
                    % record.display_name
                )

    def _get_invoice_partner(self):
        self.ensure_one()
        return self.invoice_partner_id or self._resolve_default_invoice_partner(self.partner_id)

    def _get_communication_partners(self):
        """Recipients of member emails, used by all membership wizards.

        Individuals always get their own emails. For organisations the company
        setting decides. The contact person comes from ``address_get``: that is
        ``partner_contact_id`` when partner_contact_address_default is
        installed, and otherwise the organisation itself.
        """
        self.ensure_one()
        member = self.partner_id
        setting = self.company_id.membership_company_mail_recipients
        if not member.is_company or setting == "member":
            return member
        recipients = self.env["res.partner"]
        if setting in ("contact_person", "contact_person_and_invoice_contact"):
            recipients |= recipients.browse(member.address_get(["contact"])["contact"])
        if setting in ("invoice_contact", "contact_person_and_invoice_contact"):
            recipients |= self._get_invoice_partner()
        return recipients or member

    def _get_default_cancel_values(self, cancel_date=False, cancel_reason=False):
        self.ensure_one()
        return self._build_cancel_values(
            cancel_date=cancel_date,
            cancel_reason=cancel_reason,
        )

    def _schedule_termination(self, **kwargs):
        today = fields.Date.context_today(self)
        for record in self:
            vals = record._get_default_cancel_values(
                # Keep the original cancellation date when only the end date is corrected.
                cancel_date=kwargs.get("date_cancelled") or record.date_cancelled,
                cancel_reason=kwargs.get("cancel_reason"),
            )
            if kwargs.get("date_end"):
                # Callers over RPC (the importer) send date strings.
                vals["date_end"] = fields.Date.to_date(kwargs["date_end"])
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
        for record in self:
            if new_state == record.state:
                # Not a transition, but re-cancelling is how a wrong end date or
                # reason gets corrected: apply the values instead of dropping them.
                if new_state in {"cancelled", "terminated"} and kwargs:
                    record._write_cancellation_values(**kwargs)
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
            if new_state == "draft":
                record._check_no_periods()
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
            elif record.state in {"cancelled", "terminated"}:
                vals.update(
                    {
                        "date_cancelled": False,
                        "date_end": False,
                        "cancel_reason": False,
                    }
                )
            record.with_context(allow_membership_state_write=True).write(vals)
        return True

    def _write_cancellation_values(self, **kwargs):
        """Update the cancellation data of an already cancelled/terminated membership."""
        self.ensure_one()
        vals = self._get_default_cancel_values(
            cancel_date=kwargs.get("date_cancelled") or self.date_cancelled,
            cancel_reason=kwargs.get("cancel_reason"),
        )
        if kwargs.get("date_end"):
            vals["date_end"] = fields.Date.to_date(kwargs["date_end"])
        self.write(vals)
        return True

    def action_submit(self):
        self._do_transition("waiting")
        return True

    def action_activate(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Activate Membership"),
            "res_model": "membership.activate.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_membership_id": self.id,
            },
        }

    def action_activate_direct(self):
        """Activate without the wizard and without sending any email.

        Used by the contact importer for historical memberships. Draft and
        terminated memberships pass through `waiting` first.
        """
        for record in self:
            if record.state in ("draft", "terminated"):
                record._do_transition("waiting")
            record._do_transition("active")
        return True

    def action_revert_to_draft(self):
        self._do_transition("draft")
        return True

    def action_reopen_waiting(self):
        """Reopen a cancelled or terminated membership (used by the contact importer)."""
        self._do_transition("waiting")
        return True

    def action_cancel_direct(self, date_cancelled=False, date_end=False, cancel_reason=False):
        """Cancel without the wizard and without emails.

        Used by the contact importer. A draft membership is submitted first and
        a terminated one is reopened, so neither draft -> cancelled nor
        terminated -> cancelled is needed. Re-running it on an already cancelled
        membership updates its dates and reason.
        """
        for record in self:
            if record.state in ("draft", "terminated"):
                record._do_transition("waiting")
            record._schedule_termination(
                date_cancelled=date_cancelled,
                date_end=date_end,
                cancel_reason=cancel_reason,
            )
        return True

    def action_cancel(self):
        self.ensure_one()
        if self.state not in ("active", "waiting", "cancelled"):
            raise UserError(
                _("Only active, waiting or cancelled memberships can be cancelled.")
            )
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

    def action_view_periods(self):
        self.ensure_one()
        action = self.env.ref(
            "association_membership.action_membership_period"
        ).read()[0]
        action["domain"] = [("membership_id", "=", self.id)]
        action["context"] = {"default_membership_id": self.id}
        return action

    def action_view_invoices(self):
        self.ensure_one()
        invoice_ids = (
            self.period_ids.mapped("invoice_id")
            | self.period_ids.mapped("refund_move_id")
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

    def _ensure_default_year_period(self):
        """Return the period of the default year, creating it if missing.

        An existing period is invoiced too when it never was: a period created
        in manual mode used to suppress the invoice for good, whatever strategy
        was chosen later.
        """
        self.ensure_one()
        period_year = self._default_period_year()
        period = self.period_ids.filtered(
            lambda period: period.membership_year == period_year
        )[:1]
        if not period:
            period = self.env["membership.period"].create(
                self._prepare_period_create_values(
                    membership_year=period_year,
                )
            )
        if not period._is_billed():
            period._apply_invoicing_strategy(
                invoice_date=fields.Date.context_today(self),
            )
        return period

    def _open_periods_from(self, year):
        """Unpaid periods of `year` and later, for the cancellation wizard."""
        self.ensure_one()
        return self.period_ids.filtered(
            lambda period: period.membership_year >= year
            and period.billing_status in ("to_invoice", "invoiced", "partial")
        )

    def action_create_period(self):
        self.ensure_one()
        self._ensure_default_year_period()
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

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
        today = fields.Date.context_today(self)
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

        pairs = []
        for record in self:
            if record.partner_id and record.company_id.partner_id and relation_type:
                pairs.append((record.partner_id.id, record.company_id.partner_id.id))

        if not pairs:
            return

        domain = [("type_id", "=", relation_type.id)]
        pair_domain = []
        for left, right in set(pairs):
            pair_domain.extend(["&", ("left_partner_id", "=", left), ("right_partner_id", "=", right)])
        for i in range(len(set(pairs)) - 1):
            pair_domain.insert(0, "|")
        domain.extend(pair_domain)

        existing_relations = relation_model.search(domain)
        relation_map = {
            (rel.left_partner_id.id, rel.right_partner_id.id): rel
            for rel in existing_relations
        }

        terminated_records = self.filtered(lambda r: r.state == "terminated")
        sibling_map = {}
        if terminated_records:
            sibling_domain = [
                ("id", "not in", terminated_records.ids),
                ("partner_id", "in", terminated_records.mapped("partner_id").ids),
                ("company_id", "in", terminated_records.mapped("company_id").ids),
                ("state", "in", BUSINESS_ACTIVE_STATES),
            ]
            siblings = self.search(sibling_domain)
            for sib in siblings:
                sibling_map.setdefault((sib.partner_id.id, sib.company_id.id), []).append(sib)

        relations_to_create = []
        created_pairs = set()

        for record in self:
            company_partner = record.company_id.partner_id
            if not record.partner_id or not company_partner or not relation_type:
                continue

            pair_key = (record.partner_id.id, company_partner.id)
            relation = relation_map.get(pair_key)

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
                elif pair_key not in created_pairs:
                    relations_to_create.append(values)
                    created_pairs.add(pair_key)
            elif record.state == "terminated" and relation:
                siblings = sibling_map.get((record.partner_id.id, record.company_id.id), [])
                active_sibling = False
                record_end = record.date_end or fields.Date.context_today(record)
                for sib in siblings:
                    if not sib.date_end or sib.date_end >= record_end:
                        active_sibling = True
                        break

                if not active_sibling:
                    relation.write({"date_end": record_end})

        if relations_to_create:
            relation_model.create(relations_to_create)

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
