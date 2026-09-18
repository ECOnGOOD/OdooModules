from odoo import Command, api, fields, models


class DonationTaxReceipt(models.Model):
    _inherit = "donation.tax.receipt"

    membership_contribution_ids = fields.One2many(
        "membership.contribution",
        "tax_receipt_id",
        string="Membership Contributions",
        readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        company_ids = {vals.get("company_id") or self.env.company.id for vals in vals_list}
        for company in self.env["res.company"].browse(company_ids):
            company._ensure_tax_receipt_sequence()
        return super().create(vals_list)

    @api.model
    def _membership_annual_contributions(self, company, start_date, end_date):
        """Paid, receipt-eligible contributions of the period without a receipt.

        Manual mode: marked as paid with a payment date in the period. Imported
        history has no payment date and is never included. Invoice mode: the
        invoice is fully paid and dated in the period.
        """
        contributions = self.env["membership.contribution"].search(
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
        return contributions.filtered(
            lambda contribution: contribution._tax_receipt_partner().tax_receipt_option == "annual"
            or (
                contribution.membership_invoicing_strategy == "manual"
                and contribution._tax_receipt_partner().tax_receipt_option == "each"
            )
        )

    @api.model
    def update_tax_receipt_annual_dict(self, tax_receipt_annual_dict, start_date, end_date, company):
        super().update_tax_receipt_annual_dict(
            tax_receipt_annual_dict, start_date, end_date, company
        )
        for contribution in self._membership_annual_contributions(company, start_date, end_date):
            partner_dict = tax_receipt_annual_dict.setdefault(
                contribution._tax_receipt_partner(),
                {"amount": 0.0, "extra_vals": {}},
            )
            # Paid contributions always carry amount_paid, in both modes.
            partner_dict["amount"] += contribution.amount_paid
            # donation_base passes extra_vals to the receipt it creates.
            partner_dict["extra_vals"].setdefault("membership_contribution_ids", []).append(
                Command.link(contribution.id)
            )
