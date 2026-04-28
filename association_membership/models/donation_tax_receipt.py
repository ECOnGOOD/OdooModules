from odoo import api, fields, models


class DonationTaxReceipt(models.Model):
    _inherit = "donation.tax.receipt"

    @api.model
    def _membership_annual_contributions_domain(self, company, start_date, end_date, partner=None):
        domain = [
            ("company_id", "=", company.id),
            ("product_id.tax_receipt_ok", "=", True),
            ("tax_receipt_id", "=", False),
            ("invoice_id.payment_state", "in", ("paid", "in_payment")),
            ("invoice_id.invoice_date", ">=", start_date),
            ("invoice_id.invoice_date", "<=", end_date),
        ]
        if partner is not None:
            domain.append(("invoice_id.commercial_partner_id", "=", partner.id))
        return domain

    @api.model
    def update_tax_receipt_annual_dict(self, tax_receipt_annual_dict, start_date, end_date, company):
        super().update_tax_receipt_annual_dict(
            tax_receipt_annual_dict, start_date, end_date, company
        )
        contributions = self.env["membership.contribution"].search(
            self._membership_annual_contributions_domain(company, start_date, end_date)
        )
        for contribution in contributions:
            partner = contribution.invoice_id.commercial_partner_id
            if not partner or partner.tax_receipt_option != "annual":
                continue
            partner_dict = tax_receipt_annual_dict.setdefault(
                partner,
                {"amount": 0.0, "extra_vals": {}},
            )
            partner_dict["amount"] += (
                contribution.amount_paid
                or contribution.amount_invoiced
                or contribution.amount
            )

    @api.model_create_multi
    def create(self, vals_list):
        receipts = super().create(vals_list)
        for receipt in receipts:
            receipt._stamp_membership_contributions()
        return receipts

    def _stamp_membership_contributions(self):
        self.ensure_one()
        if self.type != "annual" or not self.partner_id or not self.donation_date:
            return
        donation_date = fields.Date.to_date(self.donation_date)
        year_start = donation_date.replace(month=1, day=1)
        year_end = donation_date.replace(month=12, day=31)
        contributions = self.env["membership.contribution"].search(
            self._membership_annual_contributions_domain(
                self.company_id, year_start, year_end, partner=self.partner_id
            )
        )
        if contributions:
            contributions.write({"tax_receipt_id": self.id})
