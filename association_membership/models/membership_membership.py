from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


MEMBERSHIP_STATE_SELECTION = [
    ("draft", "Draft"),
    ("waiting", "Waiting"),
    ("active", "Active"),
    ("cancelled", "Cancelled"),
]


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
        string="Billing Contact",
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
    )
    date_start = fields.Date(required=True, tracking=True, index=True)
    date_end = fields.Date(tracking=True, index=True)
    date_cancelled = fields.Date(tracking=True, index=True)
    cancel_reason = fields.Text(tracking=True)
    external_ref = fields.Char(index=True, copy=False)
    amount_override = fields.Monetary(tracking=True)
    has_amount_override = fields.Boolean(copy=False)
    is_free_override = fields.Boolean(tracking=True)
    has_free_override = fields.Boolean(copy=False)
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

    _sql_constraints = [
        (
            "membership_external_ref_company_uniq",
            "unique(company_id, external_ref)",
            "The external reference must be unique per company.",
        )
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


    @api.onchange("partner_id")
    def _onchange_partner_id(self):
        if self.partner_id and not self.invoice_partner_id:
            self.invoice_partner_id = self.partner_id

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
                    _("There is already a membership for this member, company, and product with overlapping dates.")
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

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = []
        for vals in vals_list:
            vals = vals.copy()
            if vals.get("partner_id") and not vals.get("invoice_partner_id"):
                vals["invoice_partner_id"] = vals["partner_id"]
            vals.setdefault("company_id", self.env.company.id)
            if "amount_override" in vals:
                vals["has_amount_override"] = self._has_explicit_amount_override_value(vals["amount_override"])
            if "is_free_override" in vals:
                vals["has_free_override"] = True
            if vals.get("state") == "cancelled":
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
            prepared_vals_list.append(vals)
        records = super().create(prepared_vals_list)
        records._sync_optional_partner_relations()
        return records

    def write(self, vals):
        if "state" in vals and not self.env.context.get("allow_membership_state_write"):
            raise UserError(_("Use the membership actions instead of writing the state directly."))
        vals = vals.copy()
        if "amount_override" in vals:
            vals["has_amount_override"] = self._has_explicit_amount_override_value(vals["amount_override"])
        if "is_free_override" in vals:
            vals["has_free_override"] = True
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
            "draft": {"waiting", "active", "cancelled"},
            "waiting": {"draft", "active", "cancelled"},
            "active": {"cancelled"},
            "cancelled": {"draft", "waiting", "active"},
        }

    def _get_invoice_partner(self):
        self.ensure_one()
        return self.invoice_partner_id or self.partner_id

    def _get_default_cancel_values(self, cancel_date=False, cancel_reason=False):
        self.ensure_one()
        return self._build_cancel_values(
            cancel_date=cancel_date,
            cancel_reason=cancel_reason,
        )

    def _do_transition(self, new_state, **kwargs):
        allowed = self._get_allowed_transitions()
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
            if new_state == "cancelled":
                vals.update(
                    record._get_default_cancel_values(
                        cancel_date=kwargs.get("date_cancelled"),
                        cancel_reason=kwargs.get("cancel_reason"),
                    )
                )
                if kwargs.get("date_end"):
                    vals["date_end"] = kwargs["date_end"]
            elif record.state == "cancelled":
                vals.update(
                    {
                        "date_cancelled": kwargs.get("date_cancelled"),
                        "date_end": kwargs.get("date_end"),
                        "cancel_reason": kwargs.get("cancel_reason"),
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

    def action_create_contribution(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Contribution"),
            "res_model": "membership.contribution",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_membership_id": self.id,
                "default_membership_year": fields.Date.context_today(self).year,
            },
        }

    def _resolve_is_free(self, contribution_override=False):
        self.ensure_one()
        if contribution_override is not False:
            return bool(contribution_override)
        if self.has_free_override:
            return bool(self.is_free_override)
        return not bool(self.product_id.list_price)

    def _resolve_amount_expected(self, is_free=False):
        self.ensure_one()
        if is_free:
            return 0.0
        if self.has_amount_override or self.amount_override not in (False, None, 0.0):
            return self.amount_override
        return self.product_id.list_price

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
                    "auto_post": company.membership_cron_auto_post,
                }
            )
            wizard.action_run()
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
            if record.state == "active":
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
            elif record.state == "cancelled" and relation:
                sibling_membership = self.search(
                    [
                        ("id", "!=", record.id),
                        ("partner_id", "=", record.partner_id.id),
                        ("company_id", "=", record.company_id.id),
                        ("state", "=", "active"),
                        "|",
                        ("date_end", "=", False),
                        ("date_end", ">=", record.date_end or fields.Date.context_today(record)),
                    ],
                    limit=1,
                )
                if not sibling_membership:
                    relation.write(
                        {
                            "date_end": record.date_end
                            or fields.Date.context_today(record)
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
