from datetime import timedelta

from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestEcongoodExtraFields(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organization_kind = cls.env.ref(
            "econgood_extra_fields.res_partner_organization_kind_organization"
        )
        cls.company_kind = cls.env.ref(
            "econgood_extra_fields.res_partner_organization_kind_company"
        )
        cls.ou_type = cls.env.ref(
            "econgood_extra_fields.res_partner_ou_type_regional_association"
        )

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

    def test_nonprofit_status_defaults_to_unknown(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Default Status",
                "company_type": "company",
                "is_company": True,
            }
        )
        self.assertEqual(partner.x_nonprofit_status, "unknown")

    def test_person_cannot_store_organization_taxonomy(self):
        with self.assertRaisesRegex(ValidationError, "company contacts"):
            self.env["res.partner"].create(
                {
                    "name": "Person With Organization Kind",
                    "company_type": "person",
                    "x_organization_kind_id": self.organization_kind.id,
                }
            )
        with self.assertRaisesRegex(ValidationError, "company contacts"):
            self.env["res.partner"].create(
                {
                    "name": "Person With OU Type",
                    "company_type": "person",
                    "x_ou_type_id": self.ou_type.id,
                }
            )

    def test_company_can_store_organization_taxonomy(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Organization Partner",
                "company_type": "company",
                "is_company": True,
                "x_organization_kind_id": self.company_kind.id,
                "x_ou_type_id": self.ou_type.id,
                "x_nonprofit_status": "confirmed",
            }
        )
        self.assertEqual(partner.x_organization_kind_id, self.company_kind)
        self.assertEqual(partner.x_ou_type_id, self.ou_type)
        self.assertEqual(partner.x_nonprofit_status, "confirmed")

    def test_lookup_code_uniqueness(self):
        with self.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env["res.partner.organization.kind"].create(
                {
                    "name": "Duplicate Company",
                    "code": self.company_kind.code,
                }
            )
            self.env.cr.flush()
