from odoo import models


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def unlink(self):
        # Unreconciling a payment (e.g. a returned direct debit) makes a paid invoice unpaid.
        invoices = (self.debit_move_id | self.credit_move_id).move_id.filtered(
            lambda move: move.move_type == "out_invoice"
        )
        result = super().unlink()
        for invoice in invoices.filtered(lambda move: move.payment_state != "paid"):
            invoice.line_ids.membership_contribution_id._flag_invalid_tax_receipts(invoice)
        return result
