from odoo import _, fields, models
from odoo.exceptions import UserError


class DonationTaxReceiptPrint(models.TransientModel):
    _inherit = "donation.tax.receipt.print"

    def print_receipts(self):
        """Print with the company's receipt PDF, e.g. the Zuwendungsbestätigung (15.2)."""
        self.ensure_one()
        if not self.receipt_ids:
            raise UserError(_("There are no tax receipts to print."))
        reports = {company._get_membership_tax_receipt_report() for company in self.receipt_ids.company_id}
        if len(reports) > 1:
            raise UserError(
                _("These receipts use different layouts. Print the receipts of one company at a time.")
            )
        self.receipt_ids.write({"print_date": fields.Date.context_today(self)})
        return reports.pop().report_action(self.receipt_ids)
