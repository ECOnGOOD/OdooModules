from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .res_company import normalize_year_value


CONTRIBUTION_BILLING_STATUS = [
    ("none", "None"),
    ("to_invoice", "To Invoice"),
    ("invoiced", "Invoiced"),
    ("partial", "Partially Paid"),
    ("paid", "Paid"),
    ("cancelled", "Cancelled"),
    ("refunded", "Refunded"),
    ("waived", "Waived"),
]


class MembershipContribution(models.Model):
    _name = "membership.contribution"
    _description = "Membership Contribution"
    _order = "membership_year desc, id desc"
    _check_company_auto = True

    membership_id = fields.Many2one(
        "membership.membership",
        required=True,
        ondelete="cascade",
        index=True,
    )
    membership_year = fields.Integer(
        required=True,
        index=True,
        default=lambda self: self._default_membership_year(),
    )
    membership_year_display = fields.Char(
        compute="_compute_membership_year_display",
        string="Year Display",
    )
    membership_year_text = fields.Char(
        string="Membership Year Input",
        compute="_compute_membership_year_text",
        inverse="_inverse_membership_year_text",
    )
    is_free = fields.Boolean(required=True, default=False)
    manual_amount_expected = fields.Monetary(
        string="Manual Expected Amount",
        default=0.0,
        copy=False,
    )
    amount_expected = fields.Monetary(
        compute="_compute_billing_fields",
        inverse="_inverse_amount_expected",
        store=True,
    )
    invoice_id = fields.Many2one("account.move", copy=False)
    refund_move_id = fields.Many2one("account.move", copy=False)
    manual_billing_status = fields.Selection(
        selection=CONTRIBUTION_BILLING_STATUS,
        default="to_invoice",
        copy=False,
    )
    billing_status = fields.Selection(
        selection=CONTRIBUTION_BILLING_STATUS,
        compute="_compute_billing_fields",
        inverse="_inverse_billing_status",
        store=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="membership_id.company_id",
        store=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        related="membership_id.partner_id",
        store=True,
        readonly=True,
    )
    invoice_line_id = fields.Many2one("account.move.line", copy=False)
    currency_id = fields.Many2one(
        "res.currency",
        related="membership_id.currency_id",
        store=True,
        readonly=True,
    )
    note = fields.Text()
    amount_invoiced = fields.Monetary(
        compute="_compute_billing_fields",
        store=True,
    )
    manual_amount_paid = fields.Monetary(
        string="Manual Paid Amount",
        default=0.0,
        copy=False,
    )
    amount_paid = fields.Monetary(
        compute="_compute_billing_fields",
        inverse="_inverse_amount_paid",
        store=True,
    )
    product_id = fields.Many2one(
        "product.product",
        related="membership_id.product_id",
        store=True,
        readonly=True,
    )
    invoice_partner_id = fields.Many2one("res.partner", string="Invoice Contact")
    date_invoice = fields.Date(
        string="Invoice Date",
        related="invoice_id.invoice_date",
        store=True,
        readonly=True,
    )
    date_refund = fields.Date(
        string="Refund Date",
        related="refund_move_id.invoice_date",
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "membership_year_uniq",
            "unique(membership_id, membership_year)",
            "Only one contribution per membership and year is allowed.",
        )
    ]

    def _auto_init(self):
        result = super()._auto_init()
        self._migrate_manual_amount_expected_values()
        return result

    def _migrate_manual_amount_expected_values(self):
        self.env.cr.execute(
            """
            SELECT column_name
              FROM information_schema.columns
             WHERE table_schema = current_schema()
               AND table_name = 'membership_contribution'
               AND column_name IN ('amount_expected', 'manual_amount_expected')
            """
        )
        available_columns = {row[0] for row in self.env.cr.fetchall()}
        if {"amount_expected", "manual_amount_expected"} - available_columns:
            return
        self.env.cr.execute(
            """
            UPDATE membership_contribution
               SET manual_amount_expected = amount_expected
             WHERE amount_expected IS NOT NULL
               AND (manual_amount_expected IS NULL OR manual_amount_expected = 0)
            """
        )

    @api.model
    def _default_membership_year(self):
        membership_id = self.env.context.get("default_membership_id")
        if membership_id:
            membership = self.env["membership.membership"].browse(membership_id)
            if membership.company_id:
                return membership.company_id.membership_default_contribution_year
        return self.env.company.membership_default_contribution_year or fields.Date.context_today(self).year

    @api.model
    def _normalize_membership_year_value(self, value):
        return normalize_year_value(value, self._fields["membership_year"].string)

    @api.depends("membership_year")
    def _compute_membership_year_display(self):
        for record in self:
            record.membership_year_display = str(record.membership_year) if record.membership_year else False

    @api.depends("membership_year")
    def _compute_membership_year_text(self):
        for record in self:
            record.membership_year_text = str(record.membership_year) if record.membership_year else False

    def _inverse_membership_year_text(self):
        for record in self:
            record.membership_year = self._normalize_membership_year_value(record.membership_year_text)

    @api.model
    def _prepare_membership_contribution_values(self, vals, membership=False):
        vals = vals.copy()
        membership = membership or self.env["membership.membership"].browse(vals["membership_id"])
        vals["membership_year"] = self._normalize_membership_year_value(
            vals.get("membership_year") or self._default_membership_year()
        )
        if "amount_expected" in vals:
            vals["manual_amount_expected"] = vals.pop("amount_expected")
        vals.setdefault("manual_amount_expected", membership._resolve_amount_expected())
        is_free = bool(vals["is_free"]) if "is_free" in vals else membership._resolve_is_free(
            amount_value=vals.get("manual_amount_expected")
        )
        vals["is_free"] = is_free
        if "amount_paid" in vals:
            vals["manual_amount_paid"] = vals.pop("amount_paid")
        vals.setdefault("manual_amount_paid", 0.0)
        if "billing_status" in vals:
            vals["manual_billing_status"] = vals.pop("billing_status")
        vals.setdefault("manual_billing_status", "waived" if is_free else "to_invoice")
        vals.setdefault("invoice_partner_id", membership._get_invoice_partner().id)
        if vals.get("invoice_line_id") and not vals.get("invoice_id"):
            line = self.env["account.move.line"].browse(vals["invoice_line_id"])
            vals["invoice_id"] = line.move_id.id
        return vals

    @api.model
    def _prepare_membership_contribution_write_values(self, vals):
        vals = vals.copy()
        if "membership_year" in vals:
            vals["membership_year"] = self._normalize_membership_year_value(vals["membership_year"])
        if "amount_expected" in vals:
            vals["manual_amount_expected"] = vals.pop("amount_expected")
        if "amount_paid" in vals:
            vals["manual_amount_paid"] = vals.pop("amount_paid")
        if "billing_status" in vals:
            vals["manual_billing_status"] = vals.pop("billing_status")
        if "manual_amount_expected" in vals:
            vals["is_free"] = float(vals["manual_amount_expected"] or 0.0) == 0.0
            if vals["is_free"]:
                vals.setdefault("manual_amount_expected", 0.0)
                vals.setdefault("manual_amount_paid", 0.0)
                vals.setdefault("manual_billing_status", "waived")
            elif "manual_billing_status" not in vals:
                vals["manual_billing_status"] = "to_invoice"
        if vals.get("invoice_line_id") and not vals.get("invoice_id"):
            line = self.env["account.move.line"].browse(vals["invoice_line_id"])
            vals["invoice_id"] = line.move_id.id
        if vals.get("is_free"):
            vals.setdefault("manual_amount_expected", 0.0)
            vals.setdefault("manual_amount_paid", 0.0)
            vals.setdefault("manual_billing_status", "waived")
        return vals

    @api.depends(
        "is_free",
        "manual_amount_expected",
        "manual_amount_paid",
        "manual_billing_status",
        "invoice_id.state",
        "invoice_id.payment_state",
        "invoice_id.amount_total",
        "invoice_id.amount_residual",
        "invoice_line_id.price_subtotal",
        "refund_move_id.state",
    )
    def _compute_billing_fields(self):
        for record in self:
            line_amount = (
                record.invoice_line_id.price_subtotal
                if record.invoice_id and record.invoice_line_id
                else 0.0
            )
            record.amount_invoiced = line_amount if record.invoice_id else 0.0
            if record.invoice_id:
                record.amount_expected = record.amount_invoiced
                if record.amount_invoiced:
                    total = record.invoice_id.amount_total or 0.0
                    if total:
                        paid_ratio = max(
                            0.0,
                            min(1.0, (total - record.invoice_id.amount_residual) / total),
                        )
                    else:
                        paid_ratio = 1.0 if record.invoice_id.payment_state in ("in_payment", "paid") else 0.0
                    record.amount_paid = record.currency_id.round(record.amount_invoiced * paid_ratio)
                else:
                    record.amount_paid = 0.0
            elif record.is_free:
                record.amount_expected = 0.0
                record.amount_paid = 0.0
            else:
                record.amount_expected = record.manual_amount_expected
                record.amount_paid = record.manual_amount_paid

            if record.is_free:
                record.billing_status = "waived"
            elif record.refund_move_id and record.refund_move_id.state == "posted":
                record.billing_status = "refunded"
            elif not record.invoice_id:
                record.billing_status = record.manual_billing_status or "to_invoice"
            elif record.invoice_id.state == "cancel":
                record.billing_status = "cancelled"
            elif record.invoice_id.payment_state in ("in_payment", "paid"):
                record.billing_status = "paid"
            elif record.invoice_id.payment_state == "partial":
                record.billing_status = "partial"
            else:
                record.billing_status = "invoiced"

    def _inverse_amount_expected(self):
        for record in self:
            record.manual_amount_expected = 0.0 if record.is_free else record.amount_expected

    def _inverse_amount_paid(self):
        for record in self:
            record.manual_amount_paid = 0.0 if record.is_free else record.amount_paid

    def _inverse_billing_status(self):
        for record in self:
            record.manual_billing_status = "waived" if record.is_free else (record.billing_status or "to_invoice")

    @api.constrains("company_id", "membership_id")
    def _check_company_matches_membership(self):
        for record in self:
            if record.company_id != record.membership_id.company_id:
                raise ValidationError(_("The contribution company must match the membership company."))

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if "membership_year" in fields_list and not defaults.get("membership_year"):
            defaults["membership_year"] = self._default_membership_year()
        membership_id = defaults.get("membership_id") or self.env.context.get("default_membership_id")
        if membership_id and "invoice_partner_id" in fields_list and not defaults.get("invoice_partner_id"):
            membership = self.env["membership.membership"].browse(membership_id)
            defaults["invoice_partner_id"] = membership._get_invoice_partner().id
        return defaults

    @api.onchange("membership_id")
    def _onchange_membership_id(self):
        if not self.membership_id:
            return
        self.invoice_partner_id = self.membership_id._get_invoice_partner()
        if not self.membership_year:
            self.membership_year = (
                self.membership_id.company_id.membership_default_contribution_year
                or self._default_membership_year()
            )

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = [self._prepare_membership_contribution_values(vals) for vals in vals_list]
        records = super().create(prepared_vals_list)
        records.filtered(
            lambda contribution: not contribution.invoice_id
            and not contribution.refund_move_id
            and not contribution.invoice_line_id
        )._sync_accounting_links_from_lines()
        if self.env.context.get("create_membership_invoice"):
            strategy = self.env.context.get("membership_invoicing_strategy")
            if not strategy:
                strategy = "auto_confirm" if self.env.context.get("membership_invoice_auto_post") else "draft"
            records._apply_invoicing_strategy(
                strategy=strategy,
                invoice_date=self.env.context.get("membership_invoice_date"),
            )
        return records

    def write(self, vals):
        vals = self._prepare_membership_contribution_write_values(vals)
        result = super().write(vals)
        if {"invoice_line_id", "invoice_id", "refund_move_id"} & set(vals):
            self._sync_accounting_links_from_lines()
        return result

    def _get_sale_journal(self, company):
        journal = self.env["account.journal"].with_company(company).search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", company.id),
            ],
            limit=1,
        )
        if not journal:
            raise UserError(_("No sales journal was found for company %s.") % company.display_name)
        return journal

    def _create_membership_invoices(self, auto_post=False, invoice_date=False):
        eligible = self.filtered(
            lambda contribution: not contribution.is_free
            and not contribution.invoice_id
            and not contribution.invoice_line_id
            and not contribution.refund_move_id
        )
        invoices = self.env["account.move"]
        grouped = defaultdict(lambda: self.env["membership.contribution"])
        for contribution in eligible.sorted(key=lambda record: (record.membership_year, record.id)):
            group_key = (
                contribution.invoice_partner_id.id,
                contribution.company_id.id,
                contribution.membership_year,
                contribution.currency_id.id,
            )
            grouped[group_key] |= contribution

        for contributions in grouped.values():
            company = contributions[0].company_id
            invoice_vals = {
                "move_type": "out_invoice",
                "partner_id": contributions[0].invoice_partner_id.id,
                "company_id": company.id,
                "currency_id": contributions[0].currency_id.id,
                "journal_id": self._get_sale_journal(company).id,
                "invoice_date": invoice_date or fields.Date.context_today(self),
                "invoice_line_ids": [],
            }
            for contribution in contributions.sorted(key=lambda record: record.id):
                invoice_vals["invoice_line_ids"].append(
                    Command.create(
                        {
                            "name": contribution.product_id.display_name,
                            "product_id": contribution.product_id.id,
                            "quantity": 1.0,
                            "price_unit": contribution.amount_expected,
                            "membership_id": contribution.membership_id.id,
                            "membership_year": contribution.membership_year,
                        }
                    )
                )
            invoice = self.env["account.move"].with_company(company).create(invoice_vals)
            line_map = defaultdict(lambda: self.env["account.move.line"])
            for line in invoice.invoice_line_ids.filtered("membership_id"):
                line_map[(line.membership_id.id, line.membership_year)] |= line
            for contribution in contributions:
                invoice_line = line_map[(contribution.membership_id.id, contribution.membership_year)]
                if len(invoice_line) != 1:
                    raise UserError(
                        _("Unable to match the generated invoice line for contribution %(year)s.")
                        % {"year": contribution.membership_year}
                    )
                invoice_line.write({"membership_contribution_id": contribution.id})
            if auto_post:
                invoice.action_post()
            invoices |= invoice
        return invoices

    def _send_membership_invoices(self):
        send_model = self.env["account.move.send"]
        for invoice in self.mapped("invoice_id").filtered(lambda move: move.state == "posted"):
            send_model._generate_and_send_invoices(invoice, sending_methods=["email"])

    def _apply_invoicing_strategy(self, strategy=False, invoice_date=False):
        invoices = self.env["account.move"]
        company_map = defaultdict(lambda: self.env["membership.contribution"])
        for contribution in self:
            company_map[contribution.company_id] |= contribution
        for company, contributions in company_map.items():
            current_strategy = strategy or company.membership_invoicing_strategy or "draft"
            if current_strategy == "manual":
                continue
            created_invoices = contributions._create_membership_invoices(
                auto_post=current_strategy in {"auto_confirm", "confirm_send"},
                invoice_date=invoice_date,
            )
            if current_strategy == "confirm_send":
                contributions.filtered(lambda contribution: contribution.invoice_id in created_invoices)._send_membership_invoices()
            invoices |= created_invoices
        return invoices

    def _sync_accounting_links_from_lines(self):
        move_line_model = self.env["account.move.line"]
        move_lines = move_line_model.search(
            [
                ("membership_contribution_id", "in", self.ids),
                ("move_id.move_type", "in", ("out_invoice", "out_refund")),
            ]
        )
        grouped_lines = defaultdict(lambda: self.env["account.move.line"])
        for line in move_lines:
            grouped_lines[line.membership_contribution_id.id] |= line
        for record in self:
            lines = grouped_lines.get(record.id, self.env["account.move.line"])
            invoice_lines = lines.filtered(lambda line: line.move_id.move_type == "out_invoice")
            refund_lines = lines.filtered(lambda line: line.move_id.move_type == "out_refund")
            values = {
                "invoice_id": invoice_lines[:1].move_id.id if invoice_lines else False,
                "invoice_line_id": invoice_lines[:1].id if invoice_lines else False,
                "refund_move_id": refund_lines[:1].move_id.id if refund_lines else False,
            }
            super(MembershipContribution, record).write(values)

    def post_refund_review_message(self, refund_move):
        for record in self:
            record.membership_id.message_post(
                body=_("A refund was posted for contribution %(year)s via %(refund)s.")
                % {
                    "year": record.membership_year,
                    "refund": refund_move.display_name,
                }
            )
