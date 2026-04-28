from odoo import api, fields, models

try:
    from num2words import num2words
except ImportError:  # pragma: no cover
    num2words = None


class DonationTaxReceipt(models.Model):
    _inherit = "donation.tax.receipt"

    de_amount_in_words = fields.Char(
        compute="_compute_de_amount_in_words",
        string="Amount in Words (DE)",
    )
    de_verzicht_aufwendungen = fields.Boolean(
        string="Verzicht auf Erstattung von Aufwendungen",
        help=(
            "Set if this receipt is issued for a waiver of reimbursement claims "
            "rather than an actual money transfer."
        ),
    )

    @api.depends("amount", "currency_id")
    def _compute_de_amount_in_words(self):
        for record in self:
            record.de_amount_in_words = record._render_de_amount_in_words()

    def _render_de_amount_in_words(self):
        self.ensure_one()
        if not self.amount:
            return ""
        if num2words is None:
            return ""
        whole = int(self.amount)
        cents = int(round((self.amount - whole) * 100))
        currency_word = "Euro" if (self.currency_id.name or "EUR").upper() == "EUR" else self.currency_id.name
        try:
            whole_words = num2words(whole, lang="de").capitalize()
        except NotImplementedError:
            return ""
        if cents:
            cent_words = num2words(cents, lang="de")
            return f"{whole_words} {currency_word} und {cent_words} Cent"
        return f"{whole_words} {currency_word}"
