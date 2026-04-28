from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase


class MembershipTestCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.category = cls.env.ref("association_membership.product_category_membership")
        cls.product = cls.env["product.product"].create({
            "name": "Annual Membership",
            "categ_id": cls.category.id,
            "list_price": 50.0,
            "tax_receipt_ok": True,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Member",
            "email": "member@example.com",
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


class TestMembershipLifecycle(MembershipTestCommon):
    def test_default_state_is_draft(self):
        membership = self._make_membership()
        self.assertEqual(membership.state, "draft")

    def test_draft_to_waiting(self):
        membership = self._make_membership()
        membership.action_submit()
        self.assertEqual(membership.state, "waiting")

    def test_revert_from_waiting_clears_nothing(self):
        membership = self._make_membership()
        membership.action_submit()
        membership.action_revert_to_draft()
        self.assertEqual(membership.state, "draft")

    def test_terminated_can_only_go_to_draft(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        membership._do_transition("terminated")
        self.assertEqual(membership.state, "terminated")
        with self.assertRaises(UserError):
            membership._do_transition("waiting")

    def test_terminated_to_draft_clears_cancel_fields(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        membership._do_transition(
            "terminated",
            date_cancelled=date.today(),
            date_end=date.today(),
            cancel_reason="left",
        )
        self.assertTrue(membership.date_cancelled)
        membership.action_revert_to_draft()
        self.assertEqual(membership.state, "draft")
        self.assertFalse(membership.date_cancelled)
        self.assertFalse(membership.date_end)
        self.assertFalse(membership.cancel_reason)

    def test_disallowed_transition_raises(self):
        membership = self._make_membership()
        with self.assertRaises(UserError):
            membership._do_transition("active")

    def test_direct_state_write_blocked(self):
        membership = self._make_membership()
        with self.assertRaises(UserError):
            membership.write({"state": "active"})


class TestMembershipUnlink(MembershipTestCommon):
    def test_unlink_allowed_from_draft(self):
        membership = self._make_membership()
        self.assertEqual(membership.state, "draft")
        membership.unlink()

    def test_unlink_blocked_when_active(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        with self.assertRaises(UserError):
            membership.unlink()

    def test_unlink_blocked_when_terminated(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        membership._do_transition("terminated")
        with self.assertRaises(UserError):
            membership.unlink()


class TestMembershipAmount(MembershipTestCommon):
    def test_amount_defaults_from_product_list_price(self):
        membership = self._make_membership()
        self.assertEqual(membership.amount, 50.0)

    def test_amount_recomputes_when_product_changes(self):
        other_product = self.env["product.product"].create({
            "name": "Premium Membership",
            "categ_id": self.category.id,
            "list_price": 200.0,
        })
        membership = self._make_membership()
        membership.product_id = other_product
        self.assertEqual(membership.amount, 200.0)

    def test_amount_writable_after_default(self):
        membership = self._make_membership()
        membership.amount = 75.0
        self.assertEqual(membership.amount, 75.0)


class TestContributionBilling(MembershipTestCommon):
    def _make_contribution(self, membership=None, **overrides):
        membership = membership or self._make_membership()
        vals = {
            "membership_id": membership.id,
            "membership_year": date.today().year,
        }
        vals.update(overrides)
        return self.env["membership.contribution"].create(vals)

    def test_amount_defaults_from_membership_amount(self):
        membership = self._make_membership()
        membership.amount = 99.0
        contribution = self._make_contribution(membership=membership)
        self.assertEqual(contribution.amount, 99.0)

    def test_zero_amount_is_free_and_waived(self):
        contribution = self._make_contribution(amount=0.0)
        self.assertTrue(contribution.is_free)
        self.assertEqual(contribution.billing_status, "waived")

    def test_nonzero_amount_with_no_invoice_is_to_invoice(self):
        contribution = self._make_contribution(amount=50.0)
        self.assertFalse(contribution.is_free)
        self.assertEqual(contribution.billing_status, "to_invoice")
        self.assertEqual(contribution.amount_paid, 0.0)


class TestInvoicingStrategies(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.membership = self._make_membership()

    def _create_contribution_with_strategy(self, strategy):
        contribution = self.env["membership.contribution"].create({
            "membership_id": self.membership.id,
            "membership_year": date.today().year,
        })
        contribution._apply_invoicing_strategy(strategy=strategy)
        return contribution

    def test_manual_strategy_creates_no_invoice(self):
        contribution = self._create_contribution_with_strategy("manual")
        self.assertFalse(contribution.invoice_id)
        self.assertEqual(contribution.billing_status, "to_invoice")

    def test_draft_strategy_creates_draft_invoice(self):
        contribution = self._create_contribution_with_strategy("draft")
        self.assertTrue(contribution.invoice_id)
        self.assertEqual(contribution.invoice_id.state, "draft")
        self.assertEqual(contribution.billing_status, "invoiced")

    def test_confirm_strategy_posts_invoice(self):
        contribution = self._create_contribution_with_strategy("confirm")
        self.assertTrue(contribution.invoice_id)
        self.assertEqual(contribution.invoice_id.state, "posted")
        self.assertEqual(contribution.billing_status, "invoiced")

    def test_free_contribution_skips_invoicing(self):
        contribution = self.env["membership.contribution"].create({
            "membership_id": self.membership.id,
            "membership_year": date.today().year,
            "amount": 0.0,
        })
        contribution._apply_invoicing_strategy(strategy="confirm")
        self.assertFalse(contribution.invoice_id)
        self.assertEqual(contribution.billing_status, "waived")


class TestTaxReceipts(MembershipTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner.tax_receipt_option = "each"

    def _make_paid_invoice(self, amount=50.0):
        membership = self._make_membership()
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "membership_year": date.today().year,
            "amount": amount,
        })
        contribution._apply_invoicing_strategy(strategy="confirm")
        invoice = contribution.invoice_id
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=invoice.ids,
        ).create({}).action_create_payments()
        return contribution, invoice

    def test_each_option_auto_issues_receipt_on_payment(self):
        contribution, invoice = self._make_paid_invoice()
        self.assertIn(invoice.payment_state, ("in_payment", "paid"))
        self.assertTrue(contribution.tax_receipt_id)
        self.assertEqual(contribution.tax_receipt_id.type, "each")
        self.assertEqual(contribution.tax_receipt_id.partner_id, self.partner)
        self.assertEqual(contribution.tax_receipt_id.amount, contribution.amount_paid)

    def test_no_receipt_when_product_not_eligible(self):
        self.product.tax_receipt_ok = False
        contribution, _ = self._make_paid_invoice()
        self.assertFalse(contribution.tax_receipt_id)

    def test_no_receipt_when_partner_option_none(self):
        self.partner.tax_receipt_option = "none"
        contribution, _ = self._make_paid_invoice()
        self.assertFalse(contribution.tax_receipt_id)

    def test_no_receipt_when_partner_option_annual(self):
        self.partner.tax_receipt_option = "annual"
        contribution, _ = self._make_paid_invoice()
        self.assertFalse(contribution.tax_receipt_id)


class TestAnnualReceiptHook(MembershipTestCommon):
    def test_annual_hook_aggregates_eligible_contributions(self):
        self.partner.tax_receipt_option = "annual"
        membership = self._make_membership()
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "membership_year": date.today().year,
            "amount": 50.0,
        })
        contribution._apply_invoicing_strategy(strategy="confirm")
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=contribution.invoice_id.ids,
        ).create({}).action_create_payments()
        # Hook population
        receipt_dict = {}
        year_start = date(date.today().year, 1, 1)
        year_end = date(date.today().year, 12, 31)
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict, year_start, year_end, self.company
        )
        commercial = self.partner.commercial_partner_id
        self.assertIn(commercial, receipt_dict)
        self.assertEqual(receipt_dict[commercial]["amount"], contribution.amount_paid)

    def test_annual_hook_skips_already_stamped_contributions(self):
        self.partner.tax_receipt_option = "annual"
        membership = self._make_membership()
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "membership_year": date.today().year,
            "amount": 50.0,
        })
        contribution._apply_invoicing_strategy(strategy="confirm")
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=contribution.invoice_id.ids,
        ).create({}).action_create_payments()
        receipt = self.env["donation.tax.receipt"].create({
            "company_id": self.company.id,
            "currency_id": self.company.currency_id.id,
            "partner_id": self.partner.commercial_partner_id.id,
            "donation_date": date(date.today().year, 12, 31),
            "amount": 50.0,
            "type": "annual",
        })
        # Annual receipt creation auto-stamps via the override
        self.assertEqual(contribution.tax_receipt_id, receipt)
        # Subsequent hook call should not re-aggregate
        receipt_dict = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict,
            date(date.today().year, 1, 1),
            date(date.today().year, 12, 31),
            self.company,
        )
        self.assertNotIn(self.partner.commercial_partner_id, receipt_dict)


class TestMembershipNumberSequence(MembershipTestCommon):
    def test_sequence_lazily_created_per_company(self):
        sequence = self.company._get_membership_number_sequence()
        self.assertEqual(sequence.code, "association.membership.number.seq")
        self.assertEqual(sequence.company_id, self.company)

    def test_membership_number_assigned_on_create(self):
        membership = self._make_membership()
        self.assertTrue(membership.membership_number)
        self.assertFalse(membership.override_membership_number)

    def test_explicit_membership_number_marks_override(self):
        membership = self._make_membership(membership_number="EXPLICIT-001")
        self.assertEqual(membership.membership_number, "EXPLICIT-001")
        self.assertTrue(membership.override_membership_number)

    def test_duplicate_membership_number_blocked(self):
        self._make_membership(membership_number="DUP-001")
        with self.assertRaises(ValidationError):
            other_partner = self.env["res.partner"].create({"name": "Other"})
            self._make_membership(
                partner_id=other_partner.id,
                membership_number="DUP-001",
            )
