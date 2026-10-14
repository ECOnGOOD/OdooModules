from datetime import timedelta

from lxml import etree
from psycopg2 import IntegrityError

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase, new_test_user


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
                    "employee_count": -1,
                }
            )
        with self.assertRaisesRegex(ValidationError, "cannot be negative"):
            self.env["res.partner"].create(
                {
                    "name": "Negative Inhabitants",
                    "is_company": True,
                    "inhabitant_count": -5,
                }
            )

    def test_future_signed_dates_not_allowed(self):
        future_date = fields.Date.today() + timedelta(days=10)
        with self.assertRaisesRegex(ValidationError, "cannot be in the future"):
            self.env["res.partner"].create(
                {
                    "name": "Future Conduct Date",
                    "is_company": True,
                    "code_of_conduct_signed_date": future_date,
                }
            )
        with self.assertRaisesRegex(ValidationError, "cannot be in the future"):
            self.env["res.partner"].create(
                {
                    "name": "Future Privacy Date",
                    "is_company": True,
                    "privacy_agreement_signed_date": future_date,
                }
            )

    def test_email_econgood_validation(self):
        with self.assertRaisesRegex(ValidationError, "not a valid email"):
            self.env["res.partner"].create(
                {
                    "name": "Invalid Email",
                    "is_company": True,
                    "email_econgood": "invalid-email",
                }
            )
        partner = self.env["res.partner"].create(
            {
                "name": "Valid Email",
                "is_company": True,
                "email_econgood": "valid@econgood.org",
            }
        )
        self.assertEqual(partner.email_econgood, "valid@econgood.org")

    def test_nonprofit_status_defaults_to_unknown(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Default Status",
                "company_type": "company",
                "is_company": True,
            }
        )
        self.assertEqual(partner.nonprofit_status, "unknown")
        self.assertFalse(partner.is_econgood_ou)

    def test_person_cannot_store_company_only_classification(self):
        with self.assertRaisesRegex(ValidationError, "company contacts"):
            self.env["res.partner"].create(
                {
                    "name": "Person With OU Flag",
                    "company_type": "person",
                    "is_econgood_ou": True,
                }
            )

    def test_company_can_store_organization_kind_when_not_ou(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Organization Partner",
                "company_type": "company",
                "is_company": True,
                "is_econgood_ou": False,
                "organization_kind_id": self.company_kind.id,
                "nonprofit_status": "confirmed",
            }
        )
        self.assertEqual(partner.organization_kind_id, self.company_kind)
        self.assertFalse(partner.ou_type_id)

    def test_company_can_store_ou_type_when_ou_enabled(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Regional Association Partner",
                "company_type": "company",
                "is_company": True,
                "is_econgood_ou": True,
                "ou_type_id": self.ou_type.id,
            }
        )
        self.assertTrue(partner.is_econgood_ou)
        self.assertEqual(partner.ou_type_id, self.ou_type)
        self.assertFalse(partner.organization_kind_id)

    def test_ou_flag_and_organization_kind_are_mutually_exclusive(self):
        with self.assertRaisesRegex(ValidationError, "Organization Kind must be empty"):
            self.env["res.partner"].create(
                {
                    "name": "Invalid OU Partner",
                    "company_type": "company",
                    "is_company": True,
                    "is_econgood_ou": True,
                    "organization_kind_id": self.organization_kind.id,
                }
            )

    def test_ou_type_requires_ou_flag(self):
        with self.assertRaisesRegex(ValidationError, "OU Type requires"):
            self.env["res.partner"].create(
                {
                    "name": "Invalid Organization Partner",
                    "company_type": "company",
                    "is_company": True,
                    "is_econgood_ou": False,
                    "ou_type_id": self.ou_type.id,
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

    def test_contacts_page_is_for_organisations_only(self):
        """16.3a: individuals have no Contacts & Addresses page."""
        arch = self.env["res.partner"].get_view(view_type="form")["arch"]
        page = etree.fromstring(arch).xpath("//page[@name='contact_addresses']")
        self.assertTrue(page)
        self.assertEqual(page[0].get("invisible"), "not is_company")

    def _form_arch(self, groups):
        user = new_test_user(self.env, login=f"form_{groups.replace(',', '_')}", groups=groups)
        arch = self.env["res.partner"].with_user(user).get_view(view_type="form")["arch"]
        return etree.fromstring(arch)

    def test_sales_purchase_page_is_for_the_full_form(self):
        """Roles: membership staff and Billing do not see Sales & Purchase."""
        for groups in (
            "base.group_user,base.group_partner_manager",
            "base.group_user,account.group_account_invoice",
        ):
            self.assertFalse(self._form_arch(groups).xpath("//page[@name='sales_purchases']"), groups)
        for groups in (
            "base.group_user,account.group_account_manager",
            "base.group_user,account.group_account_readonly",
            "base.group_system",
        ):
            self.assertTrue(self._form_arch(groups).xpath("//page[@name='sales_purchases']"), groups)

    def test_full_form_group_is_implied(self):
        group = "econgood_extra_fields.group_partner_full_form"
        for groups, expected in (
            ("base.group_user,account.group_account_manager", True),
            ("base.group_user,account.group_account_readonly", True),
            ("base.group_system", True),
            ("base.group_user,account.group_account_invoice", False),
            ("base.group_user,base.group_partner_manager", False),
        ):
            user = new_test_user(self.env, login=f"full_{groups.replace(',', '_')}", groups=groups)
            self.assertEqual(user.has_group(group), expected, groups)

    def test_payment_mode_once_under_invoicing(self):
        """Membership staff record the member's payment mode under Invoicing."""
        for groups in (
            "base.group_user,base.group_partner_manager",
            "base.group_user,account.group_account_manager",
        ):
            arch = self._form_arch(groups)
            fields = arch.xpath("//field[@name='customer_payment_mode_id']")
            self.assertEqual(len(fields), 1, groups)
            self.assertTrue(fields[0].xpath("ancestor::page[@name='accounting']"), groups)

    def test_invoicing_page_shows_staff_only_the_payment(self):
        staff = self._form_arch("base.group_user,base.group_partner_manager")
        self.assertTrue(staff.xpath("//page[@name='accounting']"))
        self.assertFalse(staff.xpath("//group[@name='accounting_automation']"))
        self.assertFalse(staff.xpath("//page[@name='accounting']//field[@name='bank_ids']"))

    def _arch_for(self, login, groups, **kwargs):
        user = new_test_user(self.env, login=login, groups=groups, **kwargs)
        return etree.fromstring(self.env["res.partner"].with_user(user).get_view(view_type="form")["arch"])

    def test_companies_are_read_only_for_non_admins(self):
        """D54: only administrators change the companies a contact is visible to."""
        # Odoo keeps the multi-company group only for users with several companies.
        second = self.env["res.company"].create({"name": "Second Association"})
        companies = [(6, 0, (self.env.company | second).ids)]
        xpath = "//page[@string='Additional Information']//field[@name='company_ids']"
        staff = self._arch_for(
            "companies_staff", "base.group_user,base.group_multi_company", company_ids=companies
        ).xpath(xpath)
        self.assertEqual([field.get("readonly") for field in staff], ["1"])
        admin = self._arch_for(
            "companies_admin", "base.group_system,base.group_multi_company", company_ids=companies
        ).xpath(xpath)
        self.assertEqual([field.get("readonly") for field in admin], [None])

    def test_only_admins_can_edit_companies_anywhere(self):
        """D54: no editable company list on the form for non-administrators."""
        second = self.env["res.company"].create({"name": "Third Association"})
        companies = [(6, 0, (self.env.company | second).ids)]
        main_form = (
            "//field[@name='company_ids'][not(ancestor::field[@name='child_ids'])]"
            "[not(@readonly='1')][not(@invisible='1')]"
        )
        child_form = "//field[@name='child_ids']//field[@name='company_ids']"
        for login, groups in (
            ("d54_staff", "base.group_user,base.group_partner_manager,base.group_multi_company"),
            ("d54_finance", "account.group_account_manager,base.group_multi_company"),
        ):
            arch = self._arch_for(login, groups, company_ids=companies)
            self.assertFalse(arch.xpath(main_form), login)
            # OCA's copy in the child contact form: read-only, the parent being set.
            self.assertEqual(
                [f.get("readonly") for f in arch.xpath(child_form)], ["parent_id != False"], login
            )

    def test_ou_fields_are_read_only_for_non_admins(self):
        """15.31: the OU flag and type are set by administrators."""
        xpath = "//group[@name='econgood_organisation']//field[@name='{}']"
        staff = self._arch_for("ou_staff", "base.group_user,base.group_partner_manager")
        admin = self._arch_for("ou_admin", "base.group_system")
        for name in ("is_econgood_ou", "ou_type_id"):
            self.assertEqual([f.get("readonly") for f in staff.xpath(xpath.format(name))], ["1"], name)
            self.assertEqual([f.get("readonly") for f in admin.xpath(xpath.format(name))], [None], name)
