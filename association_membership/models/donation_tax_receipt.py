from odoo import Command, api, fields, models


class DonationTaxReceipt(models.Model):
    _inherit = "donation.tax.receipt"

    membership_period_ids = fields.One2many(
        "membership.period",
        "tax_receipt_id",
        string="Membership Periods",
        readonly=True,
    )
    membership_period_count = fields.Integer(
        compute="_compute_membership_period_count",
    )

    @api.depends("membership_period_ids")
    def _compute_membership_period_count(self):
        for receipt in self:
            receipt.membership_period_count = len(receipt.membership_period_ids)

    def action_view_membership_periods(self):
        self.ensure_one()
        action = self.env.ref("association_membership.action_membership_period").read()[0]
        action["domain"] = [("tax_receipt_id", "=", self.id)]
        action["context"] = {"create": False}
        return action

    @api.model_create_multi
    def create(self, vals_list):
        company_ids = {vals.get("company_id") or self.env.company.id for vals in vals_list}
        for company in self.env["res.company"].browse(company_ids):
            company._ensure_tax_receipt_sequence()
        return super().create(vals_list)

    @api.model
    def _membership_annual_periods(self, company, start_date, end_date):
        """Paid, receipt-eligible periods of the period without a receipt.

        Manual mode: marked as paid with a payment date in the period. Imported
        history has no payment date and is never included. Invoice mode: the
        invoice is fully paid and dated in the period.
        """
        periods = self.env["membership.period"].search(
            [
                ("company_id", "=", company.id),
                ("product_id.tax_receipt_ok", "=", True),
                ("tax_receipt_id", "=", False),
                "|",
                "&", "&", "&",
                ("membership_invoicing_strategy", "=", "manual"),
                ("billing_status", "=", "paid"),
                ("date_paid", ">=", start_date),
                ("date_paid", "<=", end_date),
                "&", "&",
                ("invoice_id.payment_state", "=", "paid"),
                ("invoice_id.invoice_date", ">=", start_date),
                ("invoice_id.invoice_date", "<=", end_date),
            ]
        )
        # Manual mode issues no per-payment receipts (decision 5), so partners
        # who chose "each" get their manually paid fees on the annual receipt.
        return periods.filtered(
            lambda period: period._tax_receipt_partner().tax_receipt_option == "annual"
            or (
                period.membership_invoicing_strategy == "manual"
                and period._tax_receipt_partner().tax_receipt_option == "each"
            )
        )

    @api.model
    def update_tax_receipt_annual_dict(self, tax_receipt_annual_dict, start_date, end_date, company):
        super().update_tax_receipt_annual_dict(
            tax_receipt_annual_dict, start_date, end_date, company
        )
        for period in self._membership_annual_periods(company, start_date, end_date):
            partner_dict = tax_receipt_annual_dict.setdefault(
                period._tax_receipt_partner(),
                {"amount": 0.0, "extra_vals": {}},
            )
            # Paid periods always carry amount_paid, in both modes.
            partner_dict["amount"] += period.amount_paid
            # donation_base passes extra_vals to the receipt it creates.
            partner_dict["extra_vals"].setdefault("membership_period_ids", []).append(
                Command.link(period.id)
            )
