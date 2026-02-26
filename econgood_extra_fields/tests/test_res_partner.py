from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestEcongoodExtraFields(TransactionCase):
    def test_negative_counts_not_allowed(self):
        with self.assertRaisesRegex(ValidationError, "cannot be negative"):
            self.env["res.partner"].create(
                {
                    "name": "Negative Employees",
                    "is_company": True,
                    "x_employee_count": -1,
                }
            )
        with self.assertRaisesRegex(ValidationError, "cannot be negative"):
            self.env["res.partner"].create(
                {
                    "name": "Negative Inhabitants",
                    "is_company": True,
                    "x_inhabitant_count": -5,
                }
            )

    def test_future_signed_dates_not_allowed(self):
        future_date = fields.Date.today() + timedelta(days=10)
        with self.assertRaisesRegex(ValidationError, "cannot be in the future"):
            self.env["res.partner"].create(
                {
                    "name": "Future Conduct Date",
                    "is_company": True,
                    "x_code_of_conduct_signed_date": future_date,
                }
            )
        with self.assertRaisesRegex(ValidationError, "cannot be in the future"):
            self.env["res.partner"].create(
                {
                    "name": "Future Privacy Date",
                    "is_company": True,
                    "x_privacy_agreement_signed_date": future_date,
                }
            )

    def test_email_econgood_validation(self):
        with self.assertRaisesRegex(ValidationError, "not a valid email"):
            self.env["res.partner"].create(
                {
                    "name": "Invalid Email",
                    "is_company": True,
                    "x_email_econgood": "invalid-email",
                }
            )
        partner = self.env["res.partner"].create(
            {
                "name": "Valid Email",
                "is_company": True,
                "x_email_econgood": "valid@example.org",
            }
        )
        self.assertEqual(partner.x_email_econgood, "valid@example.org")
