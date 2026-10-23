from collections import defaultdict

from odoo import api, fields, models
from odoo.tools import frozendict


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.depends("invoice_line_ids.membership_period_id")
    def _compute_needed_terms(self):
        """Installments of a membership invoice fall on fixed dates (D65).

        Odoo's payment terms count days from the invoice date, so they cannot
        say "the 1st of each remaining month". The shipped installment terms
        mark the invoice; the dates and shares come from its periods. Without
        periods (a credit note, an invoice made by hand) the term's own lines
        apply.
        """
        super()._compute_needed_terms()
        for invoice in self:
            if not invoice.invoice_payment_term_id.membership_payment_frequency:
                continue
            weights = invoice._membership_installment_weights()
            if not weights or not invoice.needed_terms:
                continue
            terms = list(invoice.needed_terms.values())
            invoice.needed_terms = invoice._membership_installment_terms(
                weights,
                balance=sum(term["balance"] for term in terms),
                amount_currency=sum(term["amount_currency"] for term in terms),
            )

    def _membership_installment_weights(self):
        """{due date: share of the invoice}, from the periods on its lines."""
        self.ensure_one()
        invoice_date = self.invoice_date or self.date or fields.Date.context_today(self)
        weights = defaultdict(float)
        for line in self.invoice_line_ids.filtered("membership_period_id"):
            starts = line.membership_period_id._installment_starts()
            for start in starts:
                # An installment already due when the invoice is issued is due at once.
                weights[max(start, invoice_date)] += line.price_total / len(starts)
        return dict(weights) if sum(weights.values()) else {}

    def _membership_installment_terms(self, weights, balance, amount_currency):
        """Split the totals over the due dates; the rounding rest goes to the last."""
        self.ensure_one()
        total_weight = sum(weights.values())
        company_currency = self.company_id.currency_id
        dates = sorted(weights)
        terms = {}
        balance_left, amount_left = balance, amount_currency
        for index, due_date in enumerate(dates):
            if index == len(dates) - 1:
                part_balance, part_amount = balance_left, amount_left
            else:
                ratio = weights[due_date] / total_weight
                part_balance = company_currency.round(balance * ratio)
                part_amount = self.currency_id.round(amount_currency * ratio)
                balance_left -= part_balance
                amount_left -= part_amount
            key = frozendict({
                "move_id": self.id,
                "date_maturity": due_date,
                "discount_date": False,
            })
            terms[key] = {
                "balance": part_balance,
                "amount_currency": part_amount,
                "discount_date": False,
                "discount_balance": 0.0,
                "discount_amount_currency": 0.0,
            }
        return terms

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
