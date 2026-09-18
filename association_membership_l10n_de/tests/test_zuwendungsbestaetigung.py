from datetime import date

from odoo.tests import TransactionCase


class TestZuwendungsbestaetigung(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.membership_invoicing_strategy = "manual"
        cls.product = cls.env["product.product"].create({
            "name": "Mitgliedschaft",
            "membership_ok": True,
            "list_price": 120.0,
            "tax_receipt_ok": True,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Erika Mustermann",
            "tax_receipt_option": "annual",
        })
        cls.year = date.today().year
        cls.env["res.lang"]._activate_lang("de_DE")

    def _render(self, receipt):
        html, _type = self.env["ir.actions.report"]._render_qweb_html(
            "association_membership_l10n_de.report_zuwendungsbestaetigung", receipt.ids
        )
        return html.decode()

    def test_amount_in_words(self):
        eur = self.env.ref("base.EUR")
        eur.active = True
        receipt = self.env["donation.tax.receipt"].new({"amount": 1234.5, "currency_id": eur.id})
        self.assertEqual(
            receipt.de_amount_in_words,
            "Eintausendzweihundertvierunddreißig Euro und fünfzig Cent",
        )

    def test_single_receipt_layout(self):
        receipt = self.env["donation.tax.receipt"].create({
            "partner_id": self.partner.id,
            "amount": 120.0,
            "type": "each",
            "donation_date": date(self.year, 3, 1),
        })
        html = self._render(receipt)
        self.assertIn("Bestätigung über Geldzuwendungen / Mitgliedsbeitrag", html)
        self.assertIn("Tag der Zuwendung", html)
        self.assertNotIn("Anlage zur Sammelbestätigung", html)

    def test_annual_receipt_lists_contributions(self):
        membership = self.env["membership.membership"].create({
            "partner_id": self.partner.id,
            "product_id": self.product.id,
            "date_start": date(self.year, 1, 1),
        })
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "membership_year": self.year,
        })
        contribution.action_mark_as_paid()
        contribution.date_paid = date(self.year, 2, 15)
        action = self.env["tax.receipt.annual.create"].create({
            "start_date": date(self.year, 1, 1),
            "end_date": date(self.year, 12, 31),
            "company_id": self.company.id,
        }).generate_annual_receipts()
        receipt = self.env["donation.tax.receipt"].search(action["domain"])
        self.assertEqual(receipt.membership_contribution_ids, contribution)
        html = self._render(receipt)
        self.assertIn("Sammelbestätigung über Geldzuwendungen / Mitgliedsbeiträge", html)
        self.assertIn("Zeitraum der Sammelbestätigung", html)
        self.assertIn("01.01.%s" % self.year, html)
        self.assertIn("keine weiteren Bestätigungen", html)
        self.assertIn("Anlage zur Sammelbestätigung", html)
        self.assertIn("15.02.%s" % self.year, html)
        self.assertIn("Einhundertzwanzig", html)
        self.assertIn("120,00", html)  # rendered in German
        self.assertRegex(receipt.number, r"^%s-\d{5}$" % self.year)
