from datetime import date, timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestMembershipContractGlue(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Company B"})

        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Member A",
                "company_id": cls.company.id,
            }
        )
        cls.membership_product = cls.env["product.product"].create(
            {
                "name": "Membership Product",
                "type": "service",
                "membership": True,
                "membership_date_from": fields.Date.today(),
                "membership_date_to": fields.Date.today() + timedelta(days=365),
                "list_price": 99.0,
            }
        )

    def _create_membership_contract(self, partner=None, company=None):
        partner = partner or self.partner
        company = company or self.company
        return self.env["contract.contract"].create(
            {
                "name": f"Membership Contract {partner.display_name}",
                "partner_id": partner.id,
                "company_id": company.id,
                "contract_type": "sale",
                "is_membership_contract": True,
            }
        )

    def test_dec31_default_enabled(self):
        contract = self._create_membership_contract()
        line = self.env["contract.line"].create(
            {
                "contract_id": contract.id,
                "name": "Membership 2026",
                "product_id": self.membership_product.id,
                "quantity": 1.0,
                "date_start": date(2026, 5, 10),
            }
        )
        self.assertEqual(line.date_end, date(2026, 12, 31))

    def test_dec31_default_disabled(self):
        self.company.membership_contract_dec31_default = False
        contract = self._create_membership_contract()
        line = self.env["contract.line"].create(
            {
                "contract_id": contract.id,
                "name": "Membership 2026",
                "product_id": self.membership_product.id,
                "quantity": 1.0,
                "date_start": date(2026, 5, 10),
            }
        )
        self.assertFalse(line.date_end)

    def test_membership_contract_company_must_match_partner(self):
        with self.assertRaises(ValidationError):
            self._create_membership_contract(company=self.company_b)

    def test_membership_line_company_defaults_and_must_match_contract(self):
        contract = self._create_membership_contract()
        self.partner.membership_contract_id = contract

        line = self.env["membership.membership_line"].create(
            {
                "membership_id": self.membership_product.id,
                "member_price": 99.0,
                "date": fields.Date.today(),
                "date_from": fields.Date.today(),
                "date_to": fields.Date.today() + timedelta(days=365),
                "partner": self.partner.id,
                "state": "invoiced",
            }
        )
        self.assertEqual(line.company_id, contract.company_id)

        with self.assertRaises(ValidationError):
            self.env["membership.membership_line"].create(
                {
                    "membership_id": self.membership_product.id,
                    "member_price": 99.0,
                    "date": fields.Date.today(),
                    "date_from": fields.Date.today(),
                    "date_to": fields.Date.today() + timedelta(days=365),
                    "partner": self.partner.id,
                    "state": "invoiced",
                    "company_id": self.company_b.id,
                }
            )
