from odoo import fields, models

from .payment_frequency import PAYMENT_FREQUENCY_SELECTION


class AccountPaymentTerm(models.Model):
    _inherit = "account.payment.term"

    # Set on the shipped installment terms only. An invoice with such a term
    # takes its due dates from its membership periods (AccountMove, D65).
    membership_payment_frequency = fields.Selection(
        selection=[item for item in PAYMENT_FREQUENCY_SELECTION if item[0] != "yearly"],
        string="Membership Installments",
        readonly=True,
        copy=False,
    )
