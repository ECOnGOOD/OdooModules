from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for refund in posted.filtered(lambda move: move.move_type == "out_refund"):
            refund.line_ids.membership_contribution_id.post_refund_review_message(refund)
            # A credit note created with "Reverse" carries no contribution links itself.
            (
                refund.line_ids | refund.reversed_entry_id.line_ids
            ).membership_contribution_id._flag_invalid_tax_receipts(refund)
        return posted

    def _invoice_paid_hook(self):
        # Called on reconciliation when an invoice becomes in_payment or paid,
        # and again on in_payment -> paid; both side effects are idempotent.
        result = super()._invoice_paid_hook()
        for invoice in self.filtered(lambda move: move.move_type == "out_invoice"):
            contributions = invoice.line_ids.membership_contribution_id
            if invoice.company_id.membership_auto_activate_on_payment:
                contributions.membership_id.action_activate_from_payment(invoice=invoice)
            for contribution in contributions:
                contribution._maybe_issue_tax_receipt(invoice)
        return result
