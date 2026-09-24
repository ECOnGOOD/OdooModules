from odoo import _, models
from odoo.tools.misc import format_date


class TaxReceiptAnnualCreate(models.TransientModel):
    _inherit = "tax.receipt.annual.create"

    def generate_annual_receipts(self):
        """Explain an empty run instead of donation_base's bare UserError.

        Odoo titles every UserError "Invalid Operation", which reads like a
        fault when nothing was due. The dialog stays open so the range can be
        changed.
        """
        self.ensure_one()
        receipts_by_partner = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipts_by_partner, self.start_date, self.end_date, self.company_id
        )
        if receipts_by_partner:
            return super().generate_annual_receipts()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "warning",
                "sticky": True,
                "title": _("No annual tax receipts to create"),
                "message": _(
                    "No paid, receipt-eligible membership periods for %(company)s"
                    " between %(start)s and %(end)s. A period counts when its"
                    " product has Tax Receipt set, the donor's receipt option is"
                    " Annual (or For Each Donation in manual mode) and it was"
                    " paid in this range."
                )
                % {
                    "company": self.company_id.display_name,
                    "start": format_date(self.env, self.start_date),
                    "end": format_date(self.env, self.end_date),
                },
            },
        }
