"""Payment frequency of a membership: installments within the calendar year (D65, D66)."""

import calendar
from datetime import date

PAYMENT_FREQUENCY_SELECTION = [
    ("yearly", "Yearly"),
    ("half_yearly", "Half-yearly"),
    ("quarterly", "Quarterly"),
    ("monthly", "Monthly"),
]

INSTALLMENTS_PER_YEAR = {
    "yearly": 1,
    "half_yearly": 2,
    "quarterly": 4,
    "monthly": 12,
}

# Checked in this order: "halbjährlich" and "vierteljährlich" contain "jährlich".
FREQUENCY_TOKENS = (
    ("half_yearly", ("halbj", "half", "semi")),
    ("quarterly", ("viertelj", "quart")),
    ("monthly", ("monat", "month")),
    ("yearly", ("jähr", "jaehr", "year", "annu")),
)


def normalize_payment_frequency(value):
    """The frequency a member stated ("monatlich", "Quarterly", ...), or False."""
    text = str(value or "").strip().lower()
    if not text:
        return False
    if text in INSTALLMENTS_PER_YEAR:
        return text
    for frequency, tokens in FREQUENCY_TOKENS:
        if any(token in text for token in tokens):
            return frequency
    return False


def installment_starts(year, frequency, date_start=False, date_end=False):
    """First days of the slices of `year` that the membership overlaps.

    The year is cut into 1, 2, 4 or 12 equal slices; a member owes the slices
    their membership touches, so a monthly payer joining on 15 October owes
    October to December. A yearly payer has one slice, the whole year, and
    always owes all of it (decision 3).
    """
    count = INSTALLMENTS_PER_YEAR[frequency or "yearly"]
    months = 12 // count
    starts = []
    for index in range(count):
        first_month = 1 + index * months
        last_month = first_month + months - 1
        start = date(year, first_month, 1)
        end = date(year, last_month, calendar.monthrange(year, last_month)[1])
        if date_start and end < date_start:
            continue
        if date_end and start > date_end:
            continue
        starts.append(start)
    return starts
