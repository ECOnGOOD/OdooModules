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
    amount = fields.Monetary(
        default=0.0,
        copy=False,
    )
    is_free = fields.Boolean(compute="_compute_is_free", store=True)
    invoice_id = fields.Many2one("account.move", copy=False)
    refund_move_id = fields.Many2one("account.move", copy=False)
    invoice_line_id = fields.Many2one("account.move.line", copy=False)
    tax_receipt_id = fields.Many2one(
        "donation.tax.receipt",
        string="Tax Receipt",
        copy=False,
        readonly=True,
    )
    amount_invoiced = fields.Monetary(compute="_compute_amount_invoiced", store=True, readonly=False)
    amount_paid = fields.Monetary(compute="_compute_amount_paid", store=True, readonly=False)
    billing_status = fields.Selection(
        selection=CONTRIBUTION_BILLING_STATUS,
        compute="_compute_billing_status",
        store=True,
        readonly=False,
    )
    company_id = fields.Many2one(
        "res.company",
        related="membership_id.company_id",
        store=True,
        readonly=True,
    )
    membership_invoicing_strategy = fields.Selection(
        related="company_id.membership_invoicing_strategy",
        readonly=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        related="membership_id.partner_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="membership_id.currency_id",
        store=True,
        readonly=True,
    )
    note = fields.Text()
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

    @api.depends("amount")
    def _compute_is_free(self):
        for record in self:
            record.is_free = float(record.amount or 0.0) == 0.0

    @api.depends("invoice_id", "invoice_line_id.price_subtotal")
    def _compute_amount_invoiced(self):
        for record in self:
            if record.membership_invoicing_strategy == "manual":
                continue
            if record.invoice_id and record.invoice_line_id:
                record.amount_invoiced = record.invoice_line_id.price_subtotal
            else:
                record.amount_invoiced = 0.0

    @api.depends(
        "invoice_id",
        "invoice_id.state",
        "invoice_id.payment_state",
        "invoice_id.amount_total",
        "invoice_id.amount_residual",
        "amount_invoiced",
    )
    def _compute_amount_paid(self):
        for record in self:
            if record.membership_invoicing_strategy == "manual":
                continue
            invoice = record.invoice_id
            if not invoice or not record.amount_invoiced:
                record.amount_paid = 0.0
                continue
            total = invoice.amount_total or 0.0
            if total:
                paid_ratio = max(
                    0.0,
                    min(1.0, (total - invoice.amount_residual) / total),
                )
            else:
                paid_ratio = 1.0 if invoice.payment_state in ("in_payment", "paid") else 0.0
            record.amount_paid = record.currency_id.round(record.amount_invoiced * paid_ratio)

    @api.depends(
        "is_free",
        "invoice_id",
        "invoice_id.state",
        "invoice_id.payment_state",
        "refund_move_id",
        "refund_move_id.state",
    )
    def _compute_billing_status(self):
        for record in self:
            if record.membership_invoicing_strategy == "manual":
                continue
            if record.is_free:
                record.billing_status = "waived"
            elif record.refund_move_id and record.refund_move_id.state == "posted":
                record.billing_status = "refunded"
            elif not record.invoice_id:
                record.billing_status = "to_invoice"
            elif record.invoice_id.state == "cancel":
                record.billing_status = "cancelled"
            elif record.invoice_id.payment_state in ("in_payment", "paid"):
                record.billing_status = "paid"
            elif record.invoice_id.payment_state == "partial":
                record.billing_status = "partial"
            else:
                record.billing_status = "invoiced"

    @api.model
    def action_open_default_year_contributions(self):
        action = self.env.ref(
            "association_membership.action_membership_contribution"
        ).read()[0]
        default_year = (
            self.env.company.membership_default_contribution_year
            or fields.Date.context_today(self).year
        )
        action["context"] = {
            "search_default_current_year": 1,
            "default_membership_year_filter": default_year,
        }
        return action

    @api.model
    def _prepare_membership_contribution_values(self, vals, membership=False):
        vals = vals.copy()
        membership = membership or self.env["membership.membership"].browse(vals["membership_id"])
        vals["membership_year"] = self._normalize_membership_year_value(
            vals.get("membership_year") or self._default_membership_year()
        )
        if "amount" not in vals:
            vals["amount"] = membership.amount or 0.0
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
        if vals.get("invoice_line_id") and not vals.get("invoice_id"):
            line = self.env["account.move.line"].browse(vals["invoice_line_id"])
            vals["invoice_id"] = line.move_id.id
        return vals

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
            strategy = self.env.context.get("membership_invoicing_strategy") or "draft"
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
                            "price_unit": contribution.amount,
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
                auto_post=current_strategy == "confirm",
                invoice_date=invoice_date,
            )
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

    def _is_tax_receipt_eligible(self, invoice):
        self.ensure_one()
        if self.tax_receipt_id:
            return False
        if not self.product_id.tax_receipt_ok:
            return False
        partner = self.invoice_partner_id or self.membership_id._get_invoice_partner()
        option = partner.commercial_partner_id.tax_receipt_option
        return option == "each"

    def _prepare_tax_receipt_values(self, invoice):
        self.ensure_one()
        partner = self.invoice_partner_id or self.membership_id._get_invoice_partner()
        return {
            "company_id": self.company_id.id,
            "currency_id": self.company_id.currency_id.id,
            "donation_date": invoice.invoice_date or fields.Date.context_today(self),
            "amount": self.amount_paid or self.amount_invoiced or self.amount,
            "type": "each",
            "partner_id": partner.commercial_partner_id.id,
        }

    def _maybe_issue_tax_receipt(self, invoice):
        self.ensure_one()
        if not self._is_tax_receipt_eligible(invoice):
            return self.env["donation.tax.receipt"]
        receipt = self.env["donation.tax.receipt"].create(self._prepare_tax_receipt_values(invoice))
        self.tax_receipt_id = receipt.id
        self.membership_id.message_post(
            body=_("Tax receipt %(receipt)s issued for contribution %(year)s.")
            % {
                "receipt": receipt.display_name,
                "year": self.membership_year,
            }
        )
        return receipt

    def action_mark_as_paid(self):
        manual_contributions = self.filtered(lambda c: c.membership_invoicing_strategy == "manual")
        if len(manual_contributions) != len(self):
            raise UserError(_("You can only manually mark contributions as paid when the company invoicing strategy is set to 'manual'."))
        for record in manual_contributions:
            record.write({
                "amount_invoiced": record.amount,
                "amount_paid": record.amount,
                "billing_status": "paid",
            })
        return True
