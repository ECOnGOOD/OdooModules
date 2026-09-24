"""Payment dates for invoiced periods (15.5).

A fee belongs to the year it was paid. Until now only "Mark as Paid" set
``date_paid``; periods whose invoice was paid get the day of its latest
payment. Receipts already issued keep their dates: a sent receipt is not
changed silently.

Idempotent.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)


def _fill_payment_dates(env):
    periods = env["membership.period"].search(
        [("date_paid", "=", False), ("invoice_id.payment_state", "=", "paid")]
    )
    for invoice in periods.invoice_id:
        periods.filtered(lambda period: period.invoice_id == invoice).date_paid = (
            invoice._membership_payment_date()
        )
    _logger.info("Payment date set on %s invoiced periods.", len(periods))


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _fill_payment_dates(env)
