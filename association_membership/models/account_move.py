from odoo import fields, models


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
            periods = invoice.line_ids.membership_period_id
            if invoice.payment_state == "paid":
                unset = periods.filtered(lambda period: not period.date_paid)
                if unset:
                    unset.date_paid = invoice._membership_payment_date()
            for period in periods:
                period._maybe_issue_tax_receipt(invoice)
        return result

    def _membership_payment_date(self):
        """The day the invoice was paid: its latest payment (15.5).

        A fee belongs to the year the money came in, whatever the invoice
        date. Credit notes are not payments.
        """
        self.ensure_one()
        receivable = self.line_ids.filtered(
            lambda line: line.account_id.account_type == "asset_receivable"
        )
        counterparts = (
            receivable.matched_credit_ids.credit_move_id | receivable.matched_debit_ids.debit_move_id
        ) - receivable
        dates = counterparts.filtered(lambda line: line.move_id.move_type != "out_refund").mapped("date")
        return max(dates) if dates else fields.Date.context_today(self)
