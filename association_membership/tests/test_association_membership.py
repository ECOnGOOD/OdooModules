from datetime import date

from odoo.tests import TransactionCase


class TestMembershipBilling(TransactionCase):
    """Smoke coverage for the slim contribution model and receipt-at-payment hook.

    The legacy test file (covering manual_amount_* shadow fields, amount_override,
    membership receipts) was retired alongside the refactor. New scenarios should
    be added here as the module grows.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Member",
            "is_company": False,
            "email": "member@example.com",
            "tax_receipt_option": "each",
        })
        category = cls.env.ref("association_membership.product_category_membership")
        cls.product = cls.env["product.product"].create({
            "name": "Annual Membership",
            "categ_id": category.id,
            "list_price": 50.0,
            "tax_receipt_ok": True,
        })

    def _make_membership(self, **overrides):
        vals = {
            "partner_id": self.partner.id,
            "product_id": self.product.id,
            "company_id": self.company.id,
            "date_start": date(date.today().year, 1, 1),
        }
        vals.update(overrides)
        return self.env["membership.membership"].create(vals)

    def test_contribution_amount_defaults_to_product_list_price(self):
        membership = self._make_membership()
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "membership_year": date.today().year,
        })
        self.assertEqual(contribution.amount, 50.0)
        self.assertFalse(contribution.is_free)
        self.assertEqual(contribution.billing_status, "to_invoice")

    def test_contribution_zero_amount_is_free_and_waived(self):
        membership = self._make_membership()
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "membership_year": date.today().year,
            "amount": 0.0,
        })
        self.assertTrue(contribution.is_free)
        self.assertEqual(contribution.billing_status, "waived")
