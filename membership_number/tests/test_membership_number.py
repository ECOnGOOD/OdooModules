from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestMembershipNumber(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Association B"})
        cls.env.user.write(
            {
                "company_id": cls.company_a.id,
                "company_ids": [(6, 0, (cls.company_a + cls.company_b).ids)],
            }
        )
        cls.partner_1 = cls.env["res.partner"].create({"name": "Member One"})
        cls.partner_2 = cls.env["res.partner"].create({"name": "Member Two"})
        cls.product = cls.env["product.product"].create(
            {
                "type": "service",
                "name": "Membership Product",
                "membership": True,
                "membership_date_from": fields.Date.today(),
                "membership_date_to": fields.Date.today() + timedelta(days=365),
                "list_price": 100.0,
            }
        )

    def _create_membership_line(self, company, partner):
        return (
            self.env["membership.membership_line"]
            .with_company(company)
            .create(
                {
                    "membership_id": self.product.id,
                    "member_price": 100.0,
                    "date": fields.Date.today(),
                    "date_from": fields.Date.today(),
                    "date_to": fields.Date.today() + timedelta(days=365),
                    "partner": partner.id,
                    "state": "invoiced",
                    "company_id": company.id,
                }
            )
        )

    def test_member_number_generated_per_company_context(self):
        self._create_membership_line(self.company_a, self.partner_1)
        number_a = self.partner_1.with_company(self.company_a).member_number
        self.assertTrue(number_a)

        self._create_membership_line(self.company_a, self.partner_1)
        self.assertEqual(number_a, self.partner_1.with_company(self.company_a).member_number)

        self._create_membership_line(self.company_b, self.partner_1)
        number_b = self.partner_1.with_company(self.company_b).member_number
        self.assertTrue(number_b)
        self.assertNotEqual(number_a, number_b)

    def test_global_uniqueness_manual_override(self):
        self.partner_1.with_company(self.company_a).member_number = "MANUAL-0001"
        with self.assertRaisesRegex(ValidationError, "globally unique"):
            self.partner_2.with_company(self.company_b).member_number = "MANUAL-0001"

    def test_global_uniqueness_for_same_partner(self):
        self.partner_1.with_company(self.company_a).member_number = "MANUAL-0002"
        with self.assertRaisesRegex(ValidationError, "globally unique"):
            self.partner_1.with_company(self.company_b).member_number = "MANUAL-0002"

    def test_all_member_numbers_display(self):
        self.partner_1.with_company(self.company_a).member_number = "A-001"
        self.partner_1.with_company(self.company_b).member_number = "B-001"

        html = self.partner_1.with_company(self.company_a).all_member_numbers_display
        text = self.partner_1.with_company(self.company_a).all_membership_numbers_display

        self.assertIn("<ul>", html)
        self.assertIn(self.company_a.name, html)
        self.assertIn(self.company_b.name, html)
        self.assertIn("A-001", html)
        self.assertIn("B-001", html)
        self.assertIn(f"{self.company_a.name}: A-001", text)
        self.assertIn(f"{self.company_b.name}: B-001", text)
