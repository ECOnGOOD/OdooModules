from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


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
    membership_year = fields.Integer(required=True, index=True)
    is_free = fields.Boolean(required=True, default=False)
    amount_expected = fields.Monetary(required=True, default=0.0)
    invoice_id = fields.Many2one("account.move", copy=False)
    refund_move_id = fields.Many2one("account.move", copy=False)
    billing_status = fields.Selection(
        selection=CONTRIBUTION_BILLING_STATUS,
        compute="_compute_billing_fields",
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
    amount_paid = fields.Monetary(
        compute="_compute_billing_fields",
        store=True,
    )
    product_id = fields.Many2one(
        "product.product",
        related="membership_id.product_id",
        store=True,
        readonly=True,
    )
    invoice_partner_id = fields.Many2one("res.partner")
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

    @api.depends(
        "is_free",
        "invoice_id.state",
        "invoice_id.payment_state",
        "invoice_id.amount_total",
        "invoice_id.amount_residual",
        "invoice_line_id.price_subtotal",
        "refund_move_id.state",
    )
    def _compute_billing_fields(self):
        for record in self:
            line_amount = record.invoice_line_id.price_subtotal if record.invoice_line_id else 0.0
            record.amount_invoiced = line_amount if record.invoice_id else 0.0
            if record.invoice_id and record.amount_invoiced:
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

    @api.constrains("company_id", "membership_id")
    def _check_company_matches_membership(self):
        for record in self:
            if record.company_id != record.membership_id.company_id:
                raise ValidationError(_("The contribution company must match the membership company."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            membership = self.env["membership.membership"].browse(vals["membership_id"])
            is_free = vals["is_free"] if "is_free" in vals else membership._resolve_is_free()
            vals.setdefault("is_free", is_free)
            vals.setdefault(
                "amount_expected",
                membership._resolve_amount_expected(is_free=is_free),
            )
            vals.setdefault("invoice_partner_id", membership._get_invoice_partner().id)
            if vals.get("invoice_line_id") and not vals.get("invoice_id"):
                line = self.env["account.move.line"].browse(vals["invoice_line_id"])
                vals["invoice_id"] = line.move_id.id
        records = super().create(vals_list)
        records.filtered(
            lambda contribution: not contribution.invoice_id
            and not contribution.refund_move_id
            and not contribution.invoice_line_id
        )._sync_accounting_links_from_lines()
        if not self.env.context.get("skip_membership_invoice_creation"):
            records._create_membership_invoices()
        return records

    def write(self, vals):
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
