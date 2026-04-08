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
        self.assertFalse(partner.x_is_econgood_ou)

    def test_person_cannot_store_company_only_classification(self):
        with self.assertRaisesRegex(ValidationError, "company contacts"):
            self.env["res.partner"].create(
                {
                    "name": "Person With OU Flag",
                    "company_type": "person",
                    "x_is_econgood_ou": True,
                }
            )

    def test_company_can_store_organization_kind_when_not_ou(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Organization Partner",
                "company_type": "company",
                "is_company": True,
                "x_is_econgood_ou": False,
                "x_organization_kind_id": self.company_kind.id,
                "x_nonprofit_status": "confirmed",
            }
        )
        self.assertEqual(partner.x_organization_kind_id, self.company_kind)
        self.assertFalse(partner.x_ou_type_id)

    def test_company_can_store_ou_type_when_ou_enabled(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Regional Association Partner",
                "company_type": "company",
                "is_company": True,
                "x_is_econgood_ou": True,
                "x_ou_type_id": self.ou_type.id,
            }
        )
        self.assertTrue(partner.x_is_econgood_ou)
        self.assertEqual(partner.x_ou_type_id, self.ou_type)
        self.assertFalse(partner.x_organization_kind_id)

    def test_ou_flag_and_organization_kind_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValidationError, "Organization Kind must be empty"):
            self.env["res.partner"].create(
                {
                    "name": "Invalid OU Partner",
                    "company_type": "company",
                    "is_company": True,
                    "x_is_econgood_ou": True,
                    "x_organization_kind_id": self.organization_kind.id,
                }
            )

    def test_ou_type_requires_ou_flag(self):
        with self.assertRaisesRegex(ValidationError, "OU Type requires"):
            self.env["res.partner"].create(
                {
                    "name": "Invalid Organization Partner",
                    "company_type": "company",
                    "is_company": True,
                    "x_is_econgood_ou": False,
                    "x_ou_type_id": self.ou_type.id,
                }
            )

    def test_lookup_code_uniqueness(self):
        with self.cr.savepoint(), self.assertRaises(IntegrityError):
            self.env["res.partner.organization.kind"].create(
                {
                    "name": "Duplicate Company",
                    "code": self.company_kind.code,
                }
            )
            self.env.cr.flush()
