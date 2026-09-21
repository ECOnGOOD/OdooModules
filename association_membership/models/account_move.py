from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for refund in posted.filtered(lambda move: move.move_type == "out_refund"):
            refund.line_ids.membership_period_id.post_refund_review_message(refund)
            # A credit note created with "Reverse" carries no period links itself.
            (
                refund.line_ids | refund.reversed_entry_id.line_ids
            ).membership_period_id._flag_invalid_tax_receipts(refund)
        return posted

    def _invoice_paid_hook(self):
        # Called on reconciliation when an invoice becomes in_payment or paid,
        # and again on in_payment -> paid; the side effect is idempotent.
        result = super()._invoice_paid_hook()
        for invoice in self.filtered(lambda move: move.move_type == "out_invoice"):
            for period in invoice.line_ids.membership_period_id:
                period._maybe_issue_tax_receipt(invoice)
        return result
