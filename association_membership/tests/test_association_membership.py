import importlib.util
from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase
from odoo.tools.safe_eval import safe_eval


class MembershipTestCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.product = cls.env["product.product"].create({
            "name": "Annual Membership",
            "membership_ok": True,
            "list_price": 50.0,
            "tax_receipt_ok": True,
        })
        cls.partner = cls.env["res.partner"].create({
            "name": "Test Member",
            "email": "member@example.com",
        })
        tier_attribute = cls.env["product.attribute"].create({
            "name": "Tier",
            "value_ids": [(0, 0, {"name": "1-10"}), (0, 0, {"name": "11-20"})],
        })
        cls.tier_template = cls.env["product.template"].create({
            "name": "Company Membership",
            "membership_ok": True,
            "list_price": 0.0,
            "attribute_line_ids": [(0, 0, {
                "attribute_id": tier_attribute.id,
                "value_ids": [(6, 0, tier_attribute.value_ids.ids)],
            })],
        })
        cls.tier_small, cls.tier_large = cls.tier_template.product_variant_ids.sorted(
            lambda variant: variant.product_template_attribute_value_ids.name
        )
        cls.tier_small.product_template_attribute_value_ids.price_extra = 100.0
        cls.tier_large.product_template_attribute_value_ids.price_extra = 200.0

    def _make_period(self, membership, **overrides):
        # A draft membership may not be billed; submitting mirrors what every
        # UI path does before a period is created.
        if membership.state == "draft":
            membership.action_submit()
        vals = {
            "membership_id": membership.id,
            "membership_year": date.today().year,
        }
        vals.update(overrides)
        return self.env["membership.period"].create(vals)

    def _make_billed_period(self, membership, strategy="draft", **overrides):
        """Period created under `strategy`, then invoiced by that strategy.

        The strategy is frozen when the period is created, so it has to be
        set on the membership first.
        """
        membership.invoicing_strategy = strategy
        period = self._make_period(membership, **overrides)
        period._apply_invoicing_strategy()
        return period

    def _run_annual_wizard(self):
        action = self.env["tax.receipt.annual.create"].create({
            "start_date": date(date.today().year, 1, 1),
            "end_date": date(date.today().year, 12, 31),
            "company_id": self.company.id,
        }).generate_annual_receipts()
        return self.env["donation.tax.receipt"].search(action["domain"])

    def _terminate(self, membership, **kwargs):
        """Active -> Cancelled -> Terminated: there is no direct step (15.12)."""
        membership._do_transition("cancelled", **kwargs)
        membership._do_transition("terminated", **kwargs)

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

    def test_terminated_cannot_go_to_active(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        self._terminate(membership)
        self.assertEqual(membership.state, "terminated")
        with self.assertRaises(UserError):
            membership._do_transition("active")

    def test_removed_transitions_raise(self):
        """15.12: no Active -> Draft/Terminated, no Waiting -> Cancelled, no Terminated -> Waiting."""
        membership = self._make_membership()
        membership.action_submit()
        with self.assertRaises(UserError):
            membership._do_transition("cancelled")
        membership._do_transition("active")
        for target in ("draft", "terminated", "waiting"):
            with self.assertRaises(UserError):
                membership._do_transition(target)
        self._terminate(membership)
        with self.assertRaises(UserError):
            membership._do_transition("waiting")

    def test_activate_direct_from_every_state(self):
        for start_state in ("draft", "waiting", "cancelled", "terminated"):
            membership = self._make_membership(
                partner_id=self.env["res.partner"].create({"name": start_state}).id,
            )
            if start_state != "draft":
                membership.action_submit()
            if start_state == "cancelled":
                membership._do_transition("active")
                membership._do_transition("cancelled", cancel_reason="left")
            if start_state == "terminated":
                membership._do_transition("active")
                self._terminate(membership, cancel_reason="left")
            self.assertEqual(membership.state, start_state)
            membership.action_activate_direct()
            self.assertEqual(membership.state, "active")
            self.assertFalse(membership.date_end)
            self.assertFalse(membership.date_welcome_sent)

    def test_reopen_waiting_from_cancelled_and_terminated(self):
        for end_state in ("cancelled", "terminated"):
            membership = self._make_membership(
                partner_id=self.env["res.partner"].create({"name": end_state}).id,
            )
            membership.action_submit()
            membership._do_transition("active")
            membership._do_transition("cancelled", cancel_reason="left")
            if end_state == "terminated":
                membership._do_transition("terminated", cancel_reason="left")
            membership.action_reopen_waiting()
            self.assertEqual(membership.state, "waiting")
            self.assertFalse(membership.date_cancelled)
            self.assertFalse(membership.date_end)
            self.assertFalse(membership.cancel_reason)

    def test_terminated_to_draft_clears_cancel_fields(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        self._terminate(
            membership,
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

    def test_unlink_blocked_with_periods(self):
        membership = self._make_membership()
        self._make_period(membership)
        with self.assertRaises(UserError):
            membership.unlink()

    def test_revert_to_draft_keeps_the_periods(self):
        """15.12: Draft may keep its periods; it still cannot be deleted or get new ones."""
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        period = self._make_period(membership)
        number = membership.membership_number
        self._terminate(membership)
        membership.action_revert_to_draft()
        self.assertEqual(membership.state, "draft")
        self.assertEqual(membership.period_ids, period)
        self.assertEqual(membership.membership_number, number)
        with self.assertRaises(UserError):
            membership.unlink()
        with self.assertRaises(ValidationError):
            self.env["membership.period"].create(
                {"membership_id": membership.id, "membership_year": date.today().year + 1}
            )

    def test_unlink_blocked_when_terminated(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        self._terminate(membership)
        with self.assertRaises(UserError):
            membership.unlink()


class TestMembershipAmount(MembershipTestCommon):
    def test_amount_defaults_from_product_list_price(self):
        membership = self._make_membership()
        self.assertEqual(membership.amount, 50.0)

    def test_amount_recomputes_when_product_changes(self):
        other_product = self.env["product.product"].create({
            "name": "Premium Membership",
            "membership_ok": True,
            "list_price": 200.0,
        })
        membership = self._make_membership()
        membership.product_id = other_product
        self.assertEqual(membership.amount, 200.0)

    def test_amount_writable_after_default(self):
        membership = self._make_membership()
        membership.amount = 75.0
        self.assertEqual(membership.amount, 75.0)

    def test_amount_includes_variant_price_extra(self):
        membership = self._make_membership(product_id=self.tier_small.id)
        self.assertEqual(membership.amount, 100.0)
        period = self._make_period(membership)
        self.assertFalse(period.is_free)
        self.assertEqual(period.billing_status, "to_invoice")


class TestMembershipTiers(MembershipTestCommon):
    def test_tier_change_keeps_past_periods(self):
        membership = self._make_membership(product_id=self.tier_small.id)
        period = self._make_period(membership)
        membership.product_id = self.tier_large
        self.assertEqual(membership.amount, 200.0)
        self.assertEqual(period.product_id, self.tier_small)
        self.assertEqual(period.amount, 100.0)
        next_period = self._make_period(
            membership, membership_year=date.today().year + 1
        )
        self.assertEqual(next_period.product_id, self.tier_large)
        self.assertEqual(next_period.amount, 200.0)

    def test_parallel_membership_of_same_type_rejected(self):
        self._make_membership(product_id=self.tier_small.id)
        with self.assertRaises(ValidationError):
            self._make_membership(product_id=self.tier_large.id)

    def test_type_change_rejected_once_periods_exist(self):
        membership = self._make_membership(product_id=self.tier_small.id)
        membership.product_id = self.product
        membership.product_id = self.tier_small
        self._make_period(membership)
        with self.assertRaises(ValidationError):
            membership.product_id = self.product


class TestPeriodBilling(MembershipTestCommon):
    def test_amount_defaults_from_membership_amount(self):
        membership = self._make_membership()
        membership.amount = 99.0
        period = self._make_period(membership)
        self.assertEqual(period.amount, 99.0)

    def test_zero_amount_is_free_and_waived(self):
        period = self._make_period(self._make_membership(), amount=0.0)
        self.assertTrue(period.is_free)
        self.assertEqual(period.billing_status, "waived")

    def test_nonzero_amount_with_no_invoice_is_to_invoice(self):
        period = self._make_period(self._make_membership(), amount=50.0)
        self.assertFalse(period.is_free)
        self.assertEqual(period.billing_status, "to_invoice")
        self.assertEqual(period.amount_paid, 0.0)


class TestInvoicingStrategies(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.membership = self._make_membership()

    def _create_period_with_strategy(self, strategy):
        return self._make_billed_period(self.membership, strategy=strategy)

    def test_manual_strategy_creates_no_invoice(self):
        period = self._create_period_with_strategy("manual")
        self.assertFalse(period.invoice_id)
        self.assertEqual(period.billing_status, "to_invoice")

    def test_draft_strategy_creates_draft_invoice(self):
        period = self._create_period_with_strategy("draft")
        self.assertTrue(period.invoice_id)
        self.assertEqual(period.invoice_id.state, "draft")
        self.assertEqual(period.billing_status, "invoiced")

    def test_confirm_strategy_posts_invoice(self):
        period = self._create_period_with_strategy("confirm")
        self.assertTrue(period.invoice_id)
        self.assertEqual(period.invoice_id.state, "posted")
        self.assertEqual(period.billing_status, "invoiced")

    def test_free_period_skips_invoicing(self):
        period = self._make_billed_period(self.membership, strategy="confirm", amount=0.0)
        self.assertFalse(period.invoice_id)
        self.assertEqual(period.billing_status, "waived")


class TestManualInvoicing(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"
        self.membership = self._make_membership()

    def test_new_period_is_to_invoice(self):
        period = self._make_period(self.membership)
        self.assertEqual(period.membership_invoicing_strategy, "manual")
        self.assertEqual(period.billing_status, "to_invoice")

    def test_free_period_is_waived(self):
        period = self._make_period(self.membership, amount=0.0)
        self.assertEqual(period.billing_status, "waived")

    def test_mark_as_paid(self):
        period = self._make_period(self.membership)
        period.action_mark_as_paid()
        self.assertEqual(period.billing_status, "paid")
        self.assertEqual(period.amount_paid, period.amount)

    def test_strategy_is_frozen_at_creation(self):
        period = self._make_period(self.membership)
        period.action_mark_as_paid()
        self.company.membership_invoicing_strategy = "draft"
        self.assertEqual(period.membership_invoicing_strategy, "manual")
        self.assertEqual(period.billing_status, "paid")


class TestTaxReceipts(MembershipTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner.tax_receipt_option = "each"

    def _make_paid_invoice(self, amount=50.0):
        membership = self._make_membership()
        period = self._make_billed_period(membership, strategy="confirm", amount=amount)
        invoice = period.invoice_id
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=invoice.ids,
        ).create({}).action_create_payments()
        return period, invoice

    def test_each_option_auto_issues_receipt_on_payment(self):
        period, invoice = self._make_paid_invoice()
        self.assertIn(invoice.payment_state, ("in_payment", "paid"))
        self.assertTrue(period.tax_receipt_id)
        self.assertEqual(period.tax_receipt_id.type, "each")
        self.assertEqual(period.tax_receipt_id.partner_id, self.partner)
        self.assertEqual(period.tax_receipt_id.amount, period.amount_paid)

    def test_no_receipt_when_product_not_eligible(self):
        self.product.tax_receipt_ok = False
        period, _ = self._make_paid_invoice()
        self.assertFalse(period.tax_receipt_id)

    def test_no_receipt_when_partner_option_none(self):
        self.partner.tax_receipt_option = "none"
        period, _ = self._make_paid_invoice()
        self.assertFalse(period.tax_receipt_id)

    def test_no_receipt_when_partner_option_annual(self):
        self.partner.tax_receipt_option = "annual"
        period, _ = self._make_paid_invoice()
        self.assertFalse(period.tax_receipt_id)

    def test_each_company_numbers_its_own_receipts(self):
        branch = self.env["res.company"].create({"name": "Branch", "parent_id": self.company.id})
        receipt = self.env["donation.tax.receipt"].create({
            "company_id": branch.id,
            "partner_id": self.partner.id,
            "amount": 10.0,
            "type": "each",
            "donation_date": date.today(),
        })
        self.assertNotEqual(receipt.number, "New")
        self.assertTrue(self.env["ir.sequence"].search_count([
            ("code", "=", "donation.tax.receipt"), ("company_id", "=", branch.id),
        ]))

    def test_refund_flags_receipt(self):
        period, invoice = self._make_paid_invoice()
        receipt = period.tax_receipt_id
        self.assertFalse(receipt.activity_ids)
        refund = invoice._reverse_moves()
        refund.action_post()
        self.assertEqual(len(receipt.activity_ids), 1)
        self.assertTrue(receipt.exists())

    def test_unreconciled_payment_flags_receipt(self):
        period, invoice = self._make_paid_invoice()
        receipt = period.tax_receipt_id
        invoice.line_ids.remove_move_reconcile()
        self.assertNotEqual(invoice.payment_state, "paid")
        self.assertEqual(len(receipt.activity_ids), 1)


class TestAnnualReceiptHook(MembershipTestCommon):
    def test_annual_hook_aggregates_eligible_periods(self):
        self.partner.tax_receipt_option = "annual"
        membership = self._make_membership()
        period = self._make_billed_period(membership, strategy="confirm", amount=50.0)
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=period.invoice_id.ids,
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
        self.assertEqual(receipt_dict[commercial]["amount"], period.amount_paid)

    def test_annual_wizard_links_periods_once(self):
        self.partner.tax_receipt_option = "annual"
        membership = self._make_membership()
        period = self._make_billed_period(membership, strategy="confirm", amount=50.0)
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=period.invoice_id.ids,
        ).create({}).action_create_payments()
        receipt = self._run_annual_wizard()
        self.assertEqual(receipt.membership_period_ids, period)
        self.assertEqual(period.tax_receipt_id, receipt)
        self.assertEqual(receipt.amount, 50.0)
        receipt_dict = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict, date(date.today().year, 1, 1), date(date.today().year, 12, 31), self.company
        )
        self.assertNotIn(self.partner.commercial_partner_id, receipt_dict)


class TestManualModeReceipts(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"

    def _paid_period(self, option, partner=None):
        partner = partner or self.env["res.partner"].create({"name": option})
        partner.tax_receipt_option = option
        membership = self._make_membership(partner_id=partner.id)
        period = self._make_period(membership, amount=50.0)
        period.action_mark_as_paid()
        return period

    def test_mark_as_paid_sets_payment_date_and_issues_no_receipt(self):
        period = self._paid_period("each")
        self.assertEqual(period.date_paid, date.today())
        self.assertFalse(period.tax_receipt_id)

    def test_annual_receipt_covers_each_and_annual_partners(self):
        annual = self._paid_period("annual")
        each = self._paid_period("each")
        none = self._paid_period("none")
        receipts = self._run_annual_wizard()
        self.assertEqual(receipts.membership_period_ids, annual | each)
        self.assertEqual(set(receipts.mapped("type")), {"annual"})
        self.assertFalse(none.tax_receipt_id)

    def test_imported_paid_history_is_not_receipted(self):
        partner = self.env["res.partner"].create({"name": "Imported", "tax_receipt_option": "annual"})
        membership = self._make_membership(partner_id=partner.id)
        # The importer writes the status directly, without a payment date.
        self._make_period(membership, amount=50.0).write(
            {"billing_status": "paid", "amount_paid": 50.0}
        )
        receipt_dict = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict, date(date.today().year, 1, 1), date(date.today().year, 12, 31), self.company
        )
        self.assertNotIn(partner, receipt_dict)

    def test_product_not_eligible_is_not_receipted(self):
        self.product.tax_receipt_ok = False
        period = self._paid_period("annual")
        receipt_dict = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict, date(date.today().year, 1, 1), date(date.today().year, 12, 31), self.company
        )
        self.assertNotIn(period._tax_receipt_partner(), receipt_dict)


class TestMembershipNumberSequence(MembershipTestCommon):
    def test_companies_share_one_counter_by_default(self):
        sequence = self.company._get_membership_number_sequence()
        self.assertEqual(sequence.code, "association.membership.number.seq")
        self.assertFalse(
            sequence.company_id,
            "the default counter is the shared one, not a per-company copy",
        )

    def test_opting_in_gives_the_company_its_own_counter(self):
        self.company.member_number_own_sequence = True
        sequence = self.company._get_membership_number_sequence()
        self.assertEqual(sequence.company_id, self.company)

    def test_membership_number_assigned_on_create(self):
        membership = self._make_membership()
        self.assertRegex(membership.membership_number, r"^MEM/%d/\d{5}$" % date.today().year)
        self.assertFalse(membership.override_membership_number)

    def test_default_numbering_has_no_gaps(self):
        """15.22: a rolled-back signup must not use up a number."""
        self.assertEqual(self.company._default_membership_number_sequence().implementation, "no_gap")

    def test_own_numbering_uses_its_own_format(self):
        self.company.member_number_own_sequence = True
        own = self.company._get_membership_number_sequence()
        own.write({"prefix": "BY-", "suffix": "-X", "padding": 3, "number_next": 7})
        membership = self._make_membership()
        self.assertEqual(membership.membership_number, "BY-007-X")

    def test_switching_own_numbering_off_archives_it(self):
        self.company.member_number_own_sequence = True
        own = self.company._get_membership_number_sequence()
        self.company.member_number_own_sequence = False
        self.assertFalse(own.active)
        self.assertFalse(self.company.member_number_own_sequence)
        self.assertEqual(
            self.company._get_membership_number_sequence(),
            self.company._default_membership_number_sequence(),
        )

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

    def _second_company_membership(self):
        """A membership in a second company, also on the default numbering."""
        other_company = self.env["res.company"].create({"name": "Second Association"})
        self.env.user.company_ids |= other_company
        product = self.env["product.product"].create({
            "name": "Membership Second",
            "membership_ok": True,
            "company_id": other_company.id,
            "list_price": 10.0,
        })
        partner = self.env["res.partner"].create({"name": "Second Member"})
        return other_company, self._make_membership(
            partner_id=partner.id,
            company_id=other_company.id,
            product_id=product.id,
        )

    def test_two_companies_on_the_default_numbering_do_not_collide(self):
        # Member numbers are globally unique; the default numbering is one
        # counter, so two companies can never draw the same number.
        first = self._make_membership()
        _other_company, second = self._second_company_membership()
        prefix = "MEM/%d/" % date.today().year
        self.assertTrue(first.membership_number.startswith(prefix))
        self.assertTrue(second.membership_number.startswith(prefix))
        self.assertNotEqual(first.membership_number, second.membership_number)

    def test_an_own_counter_never_restarts_behind_the_shared_one(self):
        # Numbers the shared counter already issued must not be handed out again.
        self._make_membership()
        self._make_membership(
            partner_id=self.env["res.partner"].create({"name": "Another"}).id,
        )
        shared = self.company._default_membership_number_sequence()
        shared_next = shared.number_next_actual

        self.company.member_number_own_sequence = True
        own = self.company._get_membership_number_sequence()
        self.env.invalidate_all()
        self.assertEqual(own.company_id, self.company)
        self.assertGreaterEqual(own.number_next_actual, shared_next)

    def test_an_existing_own_counter_is_lifted_when_switching_back_on(self):
        self.company.member_number_own_sequence = True
        own = self.company._get_membership_number_sequence()
        own.sudo().write({"number_next": 1})
        self.company.member_number_own_sequence = False

        shared = self.company._default_membership_number_sequence()
        shared.sudo().write({"number_next": 50})
        self.env.invalidate_all()
        self.company.member_number_own_sequence = True
        self.env.invalidate_all()
        self.assertGreaterEqual(
            self.company._get_membership_number_sequence().number_next_actual, 50
        )

    def test_an_own_counter_is_independent_once_enabled(self):
        self.company.member_number_own_sequence = True
        own = self.company._get_membership_number_sequence()
        shared = self.company._default_membership_number_sequence()
        before = shared.number_next_actual
        self._make_membership()
        self.assertEqual(shared.number_next_actual, before)
        self.assertGreater(own.number_next_actual, 0)


class TestMembershipCancellationRules(MembershipTestCommon):
    def test_cancel_fields_blocked_outside_cancel_states(self):
        with self.assertRaises(ValidationError):
            self._make_membership(date_end=date(date.today().year, 12, 31))
        membership = self._make_membership()
        membership.action_submit()
        with self.assertRaises(ValidationError):
            membership.write({"date_cancelled": date.today()})
        membership._do_transition("active")
        with self.assertRaises(ValidationError):
            membership.write({"cancel_reason": "leaving"})

    def test_waiting_is_reverted_not_cancelled(self):
        membership = self._make_membership()
        membership.action_submit()
        with self.assertRaises(UserError):
            membership.action_cancel()
        membership.action_revert_to_draft()
        self.assertEqual(membership.state, "draft")

    def test_cancel_from_active(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        year_end = date(date.today().year, 12, 31)
        membership._do_transition("cancelled", date_end=year_end)
        self.assertEqual(membership.state, "cancelled")
        self.assertTrue(membership.date_cancelled)
        self.assertEqual(membership.date_end, year_end)

    def test_cancel_terminates_when_end_date_past(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        membership._schedule_termination(
            date_cancelled=date.today(),
            date_end=date.today(),
        )
        self.assertEqual(membership.state, "terminated")

    def test_revert_to_draft_from_cancelled_clears_cancel_fields(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        membership._do_transition("cancelled", date_end=date(date.today().year, 12, 31))
        membership.action_revert_to_draft()
        self.assertEqual(membership.state, "draft")
        self.assertFalse(membership.date_cancelled)
        self.assertFalse(membership.date_end)
        self.assertFalse(membership.cancel_reason)


class TestMembershipWizardRecipients(MembershipTestCommon):
    def _make_wizards(self, membership):
        activate = (
            self.env["membership.activate.wizard"]
            .with_context(default_membership_id=membership.id)
            .create({})
        )
        cancel = (
            self.env["membership.cancel.wizard"]
            .with_context(default_membership_id=membership.id)
            .create({"cancel_reason": "Moved away"})
        )
        return activate, cancel

    def test_activate_wizard_recipients_default_to_member(self):
        membership = self._make_membership()
        membership.action_submit()
        activate, _cancel = self._make_wizards(membership)
        self.assertIn(membership.partner_id, activate.mail_partner_ids)

    def test_cancel_wizard_recipients_default_to_member(self):
        membership = self._make_membership()
        membership.action_submit()
        _activate, cancel = self._make_wizards(membership)
        self.assertIn(membership.partner_id, cancel.mail_partner_ids)

    def test_add_invoice_partner_to_wizard_recipients(self):
        invoice_partner = self.env["res.partner"].create(
            {"name": "Invoice Contact", "type": "invoice", "parent_id": self.partner.id}
        )
        membership = self._make_membership()
        membership.action_submit()
        activate, cancel = self._make_wizards(membership)
        self.assertFalse(activate.invoice_partner_included)
        activate.action_add_invoice_partner()
        self.assertIn(invoice_partner, activate.mail_partner_ids)
        self.assertTrue(activate.invoice_partner_included)
        cancel.action_add_invoice_partner()
        self.assertIn(invoice_partner, cancel.mail_partner_ids)

    def test_add_invoice_partner_is_idempotent(self):
        invoice_partner = self.env["res.partner"].create(
            {"name": "Invoice Contact", "type": "invoice", "parent_id": self.partner.id}
        )
        membership = self._make_membership()
        membership.action_submit()
        activate, _cancel = self._make_wizards(membership)
        activate.action_add_invoice_partner()
        activate.action_add_invoice_partner()
        self.assertEqual(len(activate.mail_partner_ids), 2)


class TestMembershipPeriodYear(MembershipTestCommon):
    def _set_override(self, value):
        self.company.membership_default_period_year = value

    def test_zero_means_current_year(self):
        self._set_override(0)
        self.assertEqual(self.company._membership_period_year(), date.today().year)

    def test_past_override_falls_back_to_current_year(self):
        self._set_override(date.today().year - 1)
        self.assertEqual(self.company._membership_period_year(), date.today().year)

    def test_future_override_wins(self):
        self._set_override(date.today().year + 1)
        self.assertEqual(self.company._membership_period_year(), date.today().year + 1)

    def test_membership_default_year_uses_override(self):
        self._set_override(date.today().year + 1)
        membership = self._make_membership()
        self.assertEqual(membership._default_period_year(), date.today().year + 1)

    def test_period_default_year_follows_override(self):
        self._set_override(date.today().year + 1)
        membership = self._make_membership()
        membership.action_submit()
        # No membership_year on purpose: the default must come from the override.
        period = self.env["membership.period"].create({
            "membership_id": membership.id,
            "amount": 50.0,
        })
        self.assertEqual(period.membership_year, date.today().year + 1)

    def test_settings_year_can_be_cleared(self):
        # The Char shadow field is gone: settings edit the integer directly.
        self.company.membership_default_period_year = date.today().year + 2
        settings = self.env["res.config.settings"].create({})
        settings.membership_default_period_year = 0
        settings.execute()
        self.assertEqual(self.company.membership_default_period_year, 0)
        self.assertEqual(self.company._membership_period_year(), date.today().year)

    def test_period_dates_span_the_full_year(self):
        membership = self._make_membership(date_start=date(date.today().year, 1, 1))
        period = self._make_period(membership)
        self.assertEqual(period.date_start, date(date.today().year, 1, 1))
        self.assertEqual(period.date_end, date(date.today().year, 12, 31))

    def test_period_dates_are_clipped_to_the_membership(self):
        joined = date(date.today().year, 4, 15)
        membership = self._make_membership(date_start=joined)
        period = self._make_period(membership)
        self.assertEqual(period.date_start, joined)
        self.assertEqual(period.date_end, date(date.today().year, 12, 31))

        membership._do_transition("active")
        leaves = date(date.today().year, 6, 30)
        membership._do_transition("cancelled", date_end=leaves, cancel_reason="left")
        self.assertEqual(period.date_end, leaves)

    def test_year_fields_are_plain_integers(self):
        # "2,026" came from a mistyped view option, not from the model.
        period_fields = self.env["membership.period"]._fields
        self.assertNotIn("membership_year_text", period_fields)
        self.assertNotIn("membership_year_display", period_fields)
        self.assertNotIn(
            "membership_default_period_year_text",
            self.env["res.config.settings"]._fields,
        )


class TestMembershipDefaultTemplates(MembershipTestCommon):
    def test_default_templates_exist_with_correct_models(self):
        expectations = (
            ("mail_template_membership_activation_invoice", "account.move"),
            ("mail_template_membership_welcome", "membership.membership"),
            ("mail_template_membership_cancellation", "membership.membership"),
        )
        for xmlid, model_name in expectations:
            template = self.env.ref("association_membership.%s" % xmlid)
            self.assertEqual(template.model_id.model, model_name)

    def test_company_gets_default_templates_assigned(self):
        self.assertTrue(self.company.membership_activation_invoice_template_id)
        self.assertTrue(self.company.membership_welcome_template_id)
        self.assertTrue(self.company.membership_cancellation_template_id)

    def test_post_init_hook_does_not_overwrite_custom_templates(self):
        from odoo.addons.association_membership import post_init_hook

        custom = self.env["mail.template"].create({
            "name": "Custom Welcome",
            "model_id": self.env["ir.model"]._get_id("membership.membership"),
        })
        self.company.membership_welcome_template_id = custom
        post_init_hook(self.env)
        self.assertEqual(self.company.membership_welcome_template_id, custom)

    def test_welcome_template_renders_member_details(self):
        membership = self._make_membership()
        template = self.env.ref("association_membership.mail_template_membership_welcome")
        rendered = template._render_field("body_html", membership.ids)[membership.id]
        self.assertIn(self.partner.name, rendered)
        self.assertIn(membership.membership_number, rendered)

    def test_cancellation_template_renders_cancellation_details(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        membership._do_transition("cancelled", cancel_reason="Moving away")
        template = self.env.ref("association_membership.mail_template_membership_cancellation")
        rendered = template._render_field("body_html", membership.ids)[membership.id]
        self.assertIn(self.partner.name, rendered)
        self.assertIn("Moving away", rendered)

    def test_template_subjects_and_recipients_render(self):
        self.partner.lang = "en_US"
        membership = self._make_membership()
        welcome = self.env.ref("association_membership.mail_template_membership_welcome")
        subject = welcome._render_field("subject", membership.ids)[membership.id]
        self.assertEqual(subject, "Welcome, %s!" % self.partner.name)
        self.assertEqual(welcome._render_lang(membership.ids)[membership.id], "en_US")
        cancellation = self.env.ref("association_membership.mail_template_membership_cancellation")
        subject = cancellation._render_field("subject", membership.ids)[membership.id]
        self.assertIn(self.company.name, subject)
        self.assertNotIn("${", subject)
        period = self._make_billed_period(membership, strategy="draft", amount=50.0)
        invoice = period.invoice_id
        invoice_template = self.env.ref(
            "association_membership.mail_template_membership_activation_invoice"
        )
        self.assertEqual(
            invoice_template._render_field("partner_to", invoice.ids)[invoice.id],
            str(self.partner.id),
        )
        subject = invoice_template._render_field("subject", invoice.ids)[invoice.id]
        self.assertNotIn("${", subject)

    def test_templates_render_in_german(self):
        self.env["res.lang"]._activate_lang("de_DE")
        self.env["ir.module.module"]._load_module_terms(
            ["association_membership"], ["de_DE"], overwrite=True
        )
        self.partner.lang = "de_DE"
        membership = self._make_membership()
        welcome = self.env.ref("association_membership.mail_template_membership_welcome")
        lang = welcome._render_lang(membership.ids)[membership.id]
        self.assertEqual(lang, "de_DE")
        german = welcome.with_context(lang=lang)
        self.assertEqual(
            german._render_field("subject", membership.ids)[membership.id],
            "Willkommen, %s!" % self.partner.name,
        )
        self.assertIn("Guten Tag", german._render_field("body_html", membership.ids)[membership.id])
        # The whole UI is translated, not only the templates.
        fields_de = self.env["membership.membership"].with_context(lang="de_DE").fields_get(
            ["state", "period_ids"], ["string", "selection"]
        )
        self.assertEqual(fields_de["period_ids"]["string"], "Beitragszeiträume")
        self.assertIn(("cancelled", "Gekündigt"), fields_de["state"]["selection"])

    def test_activation_invoice_template_renders(self):
        membership = self._make_membership()
        period = self._make_billed_period(membership, strategy="draft", amount=50.0)
        invoice = period.invoice_id
        template = self.env.ref("association_membership.mail_template_membership_activation_invoice")
        rendered = template._render_field("body_html", invoice.ids)[invoice.id]
        self.assertIn(self.partner.name, rendered)

    def test_settings_member_number_preview(self):
        sequence = self.company._get_membership_number_sequence()
        settings = self.env["res.config.settings"].create({})
        expected = "MEM/%d/%s" % (
            date.today().year,
            str(sequence.number_next_actual).zfill(5),
        )
        self.assertEqual(settings.member_number_preview, expected)

    def test_settings_edit_the_own_format_only(self):
        default = self.company._default_membership_number_sequence()
        settings = self.env["res.config.settings"].create({
            "member_number_prefix": "IGNORED/",
        })
        settings.execute()
        self.assertEqual(default.prefix, "MEM/%(year)s/")
        settings = self.env["res.config.settings"].create({
            "member_number_own_sequence": True,
            "member_number_prefix": "OWN/",
            "member_number_padding": 3,
        })
        settings.execute()
        own = self.company._get_membership_number_sequence()
        self.assertEqual(own.company_id, self.company)
        self.assertEqual((own.prefix, own.padding), ("OWN/", 3))
        self.assertEqual(default.prefix, "MEM/%(year)s/")

    def test_settings_next_number_follows_the_shared_counter_while_sharing(self):
        settings = self.env["res.config.settings"].create({})
        shared = self.company._default_membership_number_sequence()
        self.assertFalse(shared.company_id)
        self.assertEqual(settings.member_number_next, shared.number_next_actual)

    def test_settings_next_number_follows_the_own_counter_when_opted_in(self):
        self.company.member_number_own_sequence = True
        settings = self.env["res.config.settings"].create({})
        own = self.company._get_membership_number_sequence()
        self.assertEqual(own.company_id, self.company)
        self.assertEqual(settings.member_number_next, own.number_next_actual)


class TestMembershipProducts(MembershipTestCommon):
    def test_product_without_membership_flag_is_rejected(self):
        plain = self.env["product.product"].create({"name": "T-Shirt", "list_price": 20.0})
        with self.assertRaises(ValidationError):
            self._make_membership(product_id=plain.id)

    def test_only_exact_company_or_no_company(self):
        branch = self.env["res.company"].create({"name": "Branch", "parent_id": self.company.id})
        parent_product = self.env["product.product"].create({
            "name": "National Membership",
            "membership_ok": True,
            "company_id": self.company.id,
        })
        with self.assertRaises(ValidationError):
            self._make_membership(company_id=branch.id, product_id=parent_product.id)
        membership = self._make_membership(company_id=branch.id)  # global product
        self.assertEqual(membership.company_id, branch)
        self.assertNotIn(parent_product, self.env["product.product"].search(membership.product_domain))

    def test_partner_type_must_match(self):
        organisation = self.env["res.partner"].create({"name": "ACME", "is_company": True})
        self.product.membership_partner_type = "person"
        with self.assertRaises(ValidationError):
            self._make_membership(partner_id=organisation.id)
        self.tier_template.membership_partner_type = "company"
        with self.assertRaises(ValidationError):
            self._make_membership(product_id=self.tier_small.id)
        self._make_membership(partner_id=organisation.id, product_id=self.tier_small.id)
        self._make_membership()  # person product, person partner

    def test_product_domain_offers_matching_active_products(self):
        self.product.membership_partner_type = "person"
        membership = self._make_membership()
        self.tier_large.active = False
        offered = self.env["product.product"].search(membership.product_domain)
        self.assertIn(self.product, offered)
        self.assertIn(self.tier_small, offered)
        self.assertNotIn(self.tier_large, offered)
        organisation = self.env["res.partner"].create({"name": "ACME", "is_company": True})
        domain = self.env["product.product"]._membership_product_domain(self.company, organisation)
        self.assertNotIn(self.product, self.env["product.product"].search(domain))


class TestMembershipRenewal(MembershipTestCommon):
    def test_archived_tier_is_skipped_with_message(self):
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership(product_id=self.tier_small.id)
        membership.action_activate_direct()
        self.tier_small.active = False
        wizard = self.env["membership.renewal.wizard"].create({
            "target_year": date.today().year + 1,
            "company_ids": [(6, 0, self.company.ids)],
        })
        wizard.action_run()
        line = wizard.result_line_ids.filtered(lambda l: l.membership_id == membership)
        self.assertEqual(line.status, "skipped")
        self.assertIn("archived", line.message)
        self.assertFalse(membership.period_ids)


class TestPaymentHooks(MembershipTestCommon):
    def test_payment_issues_receipt_once_and_leaves_state_alone(self):
        self.partner.tax_receipt_option = "each"
        membership = self._make_membership()
        membership.action_submit()
        period = self._make_billed_period(membership, strategy="confirm", amount=50.0)
        invoice = period.invoice_id
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=invoice.ids,
        ).create({}).action_create_payments()
        # Payment no longer activates: activation happens before payment.
        self.assertEqual(membership.state, "waiting")
        self.assertEqual(period.billing_status, "paid")
        receipt = period.tax_receipt_id
        self.assertTrue(receipt)
        message_count = len(membership.message_ids)
        invoice._invoice_paid_hook()
        self.assertEqual(period.tax_receipt_id, receipt)
        self.assertEqual(len(membership.message_ids), message_count)

    def test_posting_a_refund_posts_one_review_message(self):
        membership = self._make_membership()
        period = self._make_billed_period(membership, strategy="confirm", amount=50.0)
        refund = period.invoice_id._reverse_moves()
        refund.invoice_line_ids.membership_period_id = period
        refund.action_post()
        messages = membership.message_ids.filtered(lambda m: "A refund was posted" in (m.body or ""))
        self.assertEqual(len(messages), 1)


class TestReactivation(MembershipTestCommon):
    def _wizard(self, membership):
        return self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({})

    def test_welcome_message_unticked_when_already_sent(self):
        membership = self._make_membership()
        membership.action_submit()
        self.assertTrue(self._wizard(membership).send_welcome_message)
        membership.date_welcome_sent = date.today()
        self.assertFalse(self._wizard(membership).send_welcome_message)

    def test_welcome_message_unticked_on_reactivation(self):
        # Imported members carry no date_welcome_sent, so the date alone is
        # not enough to recognise a second activation.
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        membership._do_transition("cancelled", cancel_reason="left")
        self.assertFalse(self._wizard(membership).send_welcome_message)

    def test_welcome_message_ticked_when_activating_from_draft(self):
        membership = self._make_membership()
        self.assertEqual(membership.state, "draft")
        self.assertTrue(self._wizard(membership).send_welcome_message)

    def test_welcome_message_unticked_after_revert_to_draft(self):
        membership = self._make_membership()
        self._make_period(membership)
        membership._do_transition("active")
        self._terminate(membership, cancel_reason="left")
        membership.action_revert_to_draft()
        self.assertFalse(self._wizard(membership).send_welcome_message)

    def test_welcome_message_unticked_after_reopen(self):
        membership = self._make_membership()
        self._make_period(membership)
        membership._do_transition("active")
        self._terminate(membership, cancel_reason="left")
        membership.action_reopen_waiting()
        self.assertEqual(membership.state, "waiting")
        self.assertFalse(self._wizard(membership).send_welcome_message)


class TestCommunicationPartners(MembershipTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organisation = cls.env["res.partner"].create({"name": "ACME", "is_company": True})
        cls.billing = cls.env["res.partner"].create({
            "name": "ACME Billing",
            "type": "invoice",
            "parent_id": cls.organisation.id,
        })

    def _recipients(self, setting):
        self.company.membership_company_mail_recipients = setting
        return self.org_membership._get_communication_partners()

    def test_individuals_always_get_their_own_emails(self):
        self.company.membership_company_mail_recipients = "invoice_contact"
        self.assertEqual(self._make_membership()._get_communication_partners(), self.partner)

    def test_organisation_settings(self):
        self.org_membership = self._make_membership(partner_id=self.organisation.id)
        # Without partner_contact_address_default the contact person is the organisation.
        self.assertEqual(self._recipients("member"), self.organisation)
        self.assertEqual(self._recipients("contact_person"), self.organisation)
        self.assertEqual(self._recipients("invoice_contact"), self.billing)
        self.assertEqual(
            self._recipients("contact_person_and_invoice_contact"),
            self.organisation | self.billing,
        )

    def test_invoice_contact_follows_the_organisation(self):
        """15.26: a change on the partner reaches existing memberships and unbilled periods."""
        organisation = self.env["res.partner"].create({"name": "Late Billing", "is_company": True})
        membership = self._make_membership(partner_id=organisation.id)
        self.assertEqual(membership.invoice_partner_id, organisation)
        period = self._make_period(membership, amount=10.0)
        self.assertEqual(period.invoice_partner_id, organisation)
        billing = self.env["res.partner"].create(
            {"name": "Late Billing Accounts", "type": "invoice", "parent_id": organisation.id}
        )
        self.assertEqual(membership.invoice_partner_id, billing)
        period._create_membership_invoices()
        self.assertEqual(period.invoice_partner_id, billing)
        self.assertEqual(period.invoice_id.partner_id, billing)
        # An issued invoice is history: a later change leaves the period alone.
        billing.type = "contact"
        self.assertEqual(membership.invoice_partner_id, organisation)
        self.assertEqual(period.invoice_partner_id, billing)

    def test_contact_person_only_for_organisations(self):
        """15.6: without a contact person set, address_get returns the organisation itself."""
        self.assertFalse(self._make_membership().contact_partner_id)
        membership = self._make_membership(partner_id=self.organisation.id)
        self.assertFalse(membership.contact_partner_id)
        self.assertEqual(membership.invoice_partner_id, self.billing)

    def test_wizards_use_the_setting(self):
        self.company.membership_company_mail_recipients = "invoice_contact"
        membership = self._make_membership(partner_id=self.organisation.id)
        membership.action_submit()
        for model, vals in (
            ("membership.activate.wizard", {}),
            # A cancellation always has to say why.
            ("membership.cancel.wizard", {"cancel_reason": "Moved away"}),
        ):
            wizard = self.env[model].with_context(default_membership_id=membership.id).create(vals)
            self.assertEqual(wizard.mail_partner_ids, self.billing)


class TestOrganisationTemplates(MembershipTestCommon):
    """15.21 (D30): optional organisation templates, falling back to the general ones."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.organisation = cls.env["res.partner"].create(
            {"name": "ACME", "is_company": True, "email": "acme@example.com"}
        )

    def _template(self, name, model):
        return self.env["mail.template"].create({
            "name": name,
            "model_id": self.env["ir.model"]._get_id(model),
            "subject": name,
            "body_html": "<p>%s</p>" % name,
        })

    def test_organisations_get_their_template_when_set(self):
        person = self._make_membership()
        organisation = self._make_membership(partner_id=self.organisation.id)
        for kind, model in (
            ("welcome", "membership.membership"),
            ("cancellation", "membership.membership"),
            ("activation_invoice", "account.move"),
        ):
            general = self.company["membership_%s_template_id" % kind]
            self.assertTrue(general)
            self.assertEqual(organisation._get_mail_template(kind), general)
            org_template = self._template("Org %s" % kind, model)
            self.company["membership_%s_org_template_id" % kind] = org_template
            self.assertEqual(organisation._get_mail_template(kind), org_template)
            self.assertEqual(person._get_mail_template(kind), general)

    def test_wizards_load_the_organisation_template(self):
        welcome = self._template("Org Welcome", "membership.membership")
        cancellation = self._template("Org Cancellation", "membership.membership")
        self.company.membership_welcome_org_template_id = welcome
        self.company.membership_cancellation_org_template_id = cancellation
        membership = self._make_membership(partner_id=self.organisation.id)
        membership.action_submit()
        activate = self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({})
        self.assertEqual(activate.welcome_template_id, welcome)
        self.assertEqual(activate.mail_subject, "Org Welcome")
        activate.action_confirm()
        cancel = self.env["membership.cancel.wizard"].with_context(
            default_membership_id=membership.id
        ).create({"cancel_reason": "Closed"})
        self.assertEqual(cancel.cancellation_template_id, cancellation)
        self.assertEqual(cancel.mail_subject, "Org Cancellation")

    def test_organisation_template_must_fit_its_use(self):
        with self.assertRaises(ValidationError):
            self.company.membership_welcome_org_template_id = self._template("Wrong", "account.move")


class TestStrategySource(MembershipTestCommon):
    """15.10: the membership and the wizard say which strategy applies."""

    def test_company_strategy_shown_and_source_named(self):
        self.company.membership_invoicing_strategy = "draft"
        membership = self._make_membership()
        membership.action_submit()
        self.assertEqual(membership.company_invoicing_strategy, "draft")
        wizard = self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({})
        self.assertEqual(wizard.invoicing_strategy, "draft")
        self.assertIn("From the settings of", wizard.invoicing_strategy_source)
        membership.invoicing_strategy = "manual"
        wizard = self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({})
        self.assertEqual(wizard.invoicing_strategy, "manual")
        self.assertIn("Set on this membership", wizard.invoicing_strategy_source)

    def test_unchanged_wizard_strategy_does_not_override_the_company(self):
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        membership.action_submit()
        self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({}).action_confirm()
        self.assertFalse(membership.invoicing_strategy)
        self.company.membership_invoicing_strategy = "draft"
        self.assertEqual(membership._get_invoicing_strategy(), "draft")


class TestActivationPeriod(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"

    def _activation_wizard(self, membership):
        return self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({"send_welcome_message": False})

    def test_creates_period_when_missing(self):
        membership = self._make_membership()
        membership.action_submit()
        wizard = self._activation_wizard(membership)
        self.assertTrue(wizard.create_period)
        wizard.action_confirm()
        self.assertEqual(membership.state, "active")
        self.assertEqual(len(membership.period_ids), 1)
        self.assertEqual(membership.period_ids.billing_status, "to_invoice")
        self.assertFalse(membership.period_ids.invoice_id)

    def test_keeps_existing_period(self):
        membership = self._make_membership()
        membership.action_submit()
        self._make_period(membership, membership_year=membership._default_period_year())
        wizard = self._activation_wizard(membership)
        self.assertTrue(wizard.has_period)
        self.assertFalse(wizard.create_period)
        wizard.action_confirm()
        self.assertEqual(len(membership.period_ids), 1)


class TestMembershipCreation(MembershipTestCommon):
    """The form is the creation UI; there is no separate creation wizard."""

    def test_started_from_partner(self):
        action = self.partner.action_create_membership()
        self.assertEqual(action["res_model"], "membership.membership")
        self.assertEqual(action["view_mode"], "form")
        self.assertEqual(action["context"]["default_partner_id"], self.partner.id)

    def test_form_previews_number_and_fee_before_saving(self):
        draft = self.env["membership.membership"].new({
            "partner_id": self.partner.id,
            "company_id": self.company.id,
            "product_id": self.tier_small.id,
        })
        self.assertEqual(draft.amount, 100.0)
        self.assertTrue(draft.membership_number_preview)
        self.assertIn(self.tier_small, self.env["product.product"].search(draft.product_domain))


class TestStrategyResolution(MembershipTestCommon):
    def test_membership_override_beats_company(self):
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        self.assertEqual(membership._get_invoicing_strategy(), "manual")
        membership.invoicing_strategy = "confirm"
        self.assertEqual(membership._get_invoicing_strategy(), "confirm")
        period = self._make_period(membership, amount=50.0)
        self.assertEqual(period.membership_invoicing_strategy, "confirm")

    def test_applied_strategy_survives_later_changes(self):
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        period = self._make_period(membership, amount=50.0)
        self.assertEqual(period.membership_invoicing_strategy, "manual")
        membership.invoicing_strategy = "confirm"
        self.company.membership_invoicing_strategy = "draft"
        self.assertEqual(period.membership_invoicing_strategy, "manual")
        self.assertEqual(period.billing_status, "to_invoice")


class TestCreateInvoiceAction(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"
        self.membership = self._make_membership()

    def test_manual_create_invoice_gives_a_draft(self):
        period = self._make_period(self.membership, amount=50.0)
        self.assertEqual(period.billing_status, "to_invoice")
        period.action_create_invoice()
        self.assertTrue(period.invoice_id)
        self.assertEqual(period.invoice_id.state, "draft")
        self.assertEqual(period.billing_status, "invoiced")

    def test_confirm_strategy_create_invoice_posts(self):
        self.membership.invoicing_strategy = "confirm"
        period = self._make_period(self.membership, amount=50.0)
        period.action_create_invoice()
        self.assertEqual(period.invoice_id.state, "posted")

    def test_manual_period_follows_its_invoice_to_paid(self):
        period = self._make_period(self.membership, amount=50.0)
        period.action_create_invoice()
        period.invoice_id.action_post()
        self.assertEqual(period.billing_status, "invoiced")
        self.assertEqual(period.amount_invoiced, 50.0)
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=period.invoice_id.ids,
        ).create({}).action_create_payments()
        self.assertEqual(period.billing_status, "paid")
        self.assertEqual(period.amount_paid, 50.0)

    def test_create_invoice_refused_when_free_or_invoiced(self):
        free = self._make_period(self.membership, amount=0.0)
        with self.assertRaises(UserError):
            free.action_create_invoice()
        other = self._make_membership(
            partner_id=self.env["res.partner"].create({"name": "Second"}).id,
        )
        billed = self._make_period(other, amount=50.0)
        billed.action_create_invoice()
        with self.assertRaises(UserError):
            billed.action_create_invoice()

    def test_mark_as_paid_refused_once_an_invoice_exists(self):
        period = self._make_period(self.membership, amount=50.0)
        period.action_create_invoice()
        with self.assertRaises(UserError):
            period.action_mark_as_paid()


class TestUnmarkAsPaid(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"
        self.membership = self._make_membership()

    def test_unmark_restores_open_period(self):
        period = self._make_period(self.membership, amount=50.0)
        period.action_mark_as_paid()
        self.assertEqual(period.billing_status, "paid")
        self.assertTrue(period.date_paid)
        period.action_unmark_as_paid()
        self.assertEqual(period.billing_status, "to_invoice")
        self.assertFalse(period.date_paid)
        self.assertEqual(period.amount_paid, 0.0)

    def test_unmark_blocked_when_a_receipt_exists(self):
        self.partner.tax_receipt_option = "annual"
        period = self._make_period(self.membership, amount=50.0)
        period.action_mark_as_paid()
        self._run_annual_wizard()
        self.assertTrue(period.tax_receipt_id)
        with self.assertRaises(UserError):
            period.action_unmark_as_paid()

    def test_imported_paid_history_keeps_its_status(self):
        period = self._make_period(self.membership, amount=50.0)
        period.write({"billing_status": "paid", "amount_paid": 50.0})
        period.invalidate_recordset()
        self.assertEqual(period.billing_status, "paid")
        self.assertEqual(period.amount_paid, 50.0)


class TestActivationInvoicing(MembershipTestCommon):
    def _activate(self, membership, **vals):
        wizard = self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({"send_welcome_message": False, **vals})
        wizard.action_confirm()
        return wizard

    def test_draft_strategy_leaves_the_invoice_in_draft(self):
        self.company.membership_invoicing_strategy = "draft"
        membership = self._make_membership()
        membership.action_submit()
        self._activate(membership)
        period = membership.period_ids
        self.assertEqual(len(period), 1)
        self.assertTrue(period.invoice_id)
        # "Draft" means draft: only `confirm` posts.
        self.assertEqual(period.invoice_id.state, "draft")
        self.assertEqual(period.billing_status, "invoiced")

    def test_confirm_strategy_can_send_the_invoice_email(self):
        self.company.membership_invoicing_strategy = "confirm"
        membership = self._make_membership()
        membership.action_submit()
        self._activate(membership, send_invoice_email=True)
        invoice = membership.period_ids.invoice_id
        self.assertEqual(invoice.state, "posted")
        self.assertTrue(invoice.message_ids)
        # One line on the membership says the invoice went out (15.9).
        self.assertTrue(
            membership.message_ids.filtered(
                lambda message: invoice.display_name in (message.body or "")
                and "sent to" in (message.body or "")
            )
        )

    def test_strategy_chosen_in_the_wizard_sticks(self):
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        membership.action_submit()
        self._activate(membership, invoicing_strategy="draft")
        self.assertEqual(membership.invoicing_strategy, "draft")
        self.assertEqual(
            membership.period_ids.membership_invoicing_strategy, "draft"
        )
        self.assertTrue(membership.period_ids.invoice_id)

    def test_existing_unbilled_period_is_invoiced_by_the_wizard(self):
        """The bug found on the live instance: a period created in manual mode
        kept its frozen strategy and suppressed the invoice for good."""
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        membership.action_submit()
        period = membership._ensure_default_year_period()
        self.assertEqual(period.membership_invoicing_strategy, "manual")
        self.assertFalse(period.invoice_id)

        self.company.membership_invoicing_strategy = "draft"
        self._activate(membership, invoicing_strategy="draft")
        self.assertEqual(membership.period_ids, period)
        self.assertEqual(period.membership_invoicing_strategy, "draft")
        self.assertTrue(period.invoice_id)
        self.assertEqual(period.invoice_id.state, "draft")

    def test_a_billed_period_keeps_its_applied_strategy(self):
        self.company.membership_invoicing_strategy = "draft"
        membership = self._make_membership()
        period = self._make_billed_period(membership, strategy="draft")
        self.company.membership_invoicing_strategy = "confirm"
        membership.invoicing_strategy = False
        period._refresh_unbilled_invoicing_strategy()
        self.assertEqual(period.membership_invoicing_strategy, "draft")

    def test_imported_paid_history_keeps_manual(self):
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        # No invoice and no payment date - exactly what the importer writes.
        period = self._make_period(membership, amount=50.0)
        period.write({"billing_status": "paid", "amount_paid": 50.0})
        self.company.membership_invoicing_strategy = "confirm"
        period._refresh_unbilled_invoicing_strategy()
        self.assertEqual(period.membership_invoicing_strategy, "manual")
        self.assertEqual(period.billing_status, "paid")

    def test_zero_fee_warns_instead_of_failing_silently(self):
        self.company.membership_invoicing_strategy = "draft"
        membership = self._make_membership(product_id=self.tier_template.product_variant_ids[0].id)
        membership.amount = 0.0
        membership.action_submit()
        wizard = self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({"send_welcome_message": False})
        self.assertTrue(wizard.free_period_warning)
        wizard.action_confirm()
        self.assertEqual(membership.period_ids.billing_status, "waived")
        self.assertFalse(membership.period_ids.invoice_id)

    def test_activate_straight_from_draft(self):
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        self.assertEqual(membership.state, "draft")
        self._activate(membership)
        self.assertEqual(membership.state, "active")
        self.assertEqual(len(membership.period_ids), 1)


class TestCancellationHandling(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"
        self.membership = self._make_membership()
        self.membership.action_submit()
        self.membership._do_transition("active")

    def _cancel_wizard(self, **vals):
        vals.setdefault("cancel_reason", "Moved away")
        return self.env["membership.cancel.wizard"].with_context(
            default_membership_id=self.membership.id
        ).create(vals)

    def test_cancellation_requires_a_reason(self):
        with self.assertRaises(Exception):
            self.env["membership.cancel.wizard"].with_context(
                default_membership_id=self.membership.id
            ).create({"date_end": date(date.today().year, 12, 31)})

    def test_end_date_can_be_corrected_while_cancelled(self):
        self._cancel_wizard(
            date_cancelled=date(date.today().year, 3, 1),
            date_end=date(date.today().year, 12, 31),
        ).action_confirm()
        self.assertEqual(self.membership.state, "cancelled")
        # Still in the future, so the membership stays cancelled rather than ending.
        corrected = date(date.today().year + 1, 6, 30)
        self._cancel_wizard(
            date_cancelled=date(date.today().year, 3, 1),
            date_end=corrected,
        ).action_confirm()
        self.assertEqual(self.membership.state, "cancelled")
        self.assertEqual(self.membership.date_end, corrected)
        self.assertEqual(self.membership.date_cancelled, date(date.today().year, 3, 1))

    def test_open_periods_can_be_dropped(self):
        period = self._make_period(self.membership, amount=50.0)
        period.action_create_invoice()
        invoice = period.invoice_id
        wizard = self._cancel_wizard(
            date_cancelled=date(date.today().year, 3, 1),
            date_end=date(date.today().year, 12, 31),
            open_period_handling="drop",
        )
        self.assertIn(period, wizard.open_period_ids)
        wizard.action_confirm()
        self.assertFalse(self.membership.period_ids)
        self.assertEqual(invoice.state, "cancel")

    def test_open_periods_are_kept_by_default(self):
        period = self._make_period(self.membership, amount=50.0)
        self._cancel_wizard(
            date_cancelled=date(date.today().year, 3, 1),
            date_end=date(date.today().year, 12, 31),
        ).action_confirm()
        self.assertEqual(self.membership.period_ids, period)

    def test_posted_invoice_period_survives_the_drop(self):
        period = self._make_period(self.membership, amount=50.0)
        period.action_create_invoice()
        period.invoice_id.action_post()
        self._cancel_wizard(
            date_cancelled=date(date.today().year, 3, 1),
            date_end=date(date.today().year, 12, 31),
            open_period_handling="drop",
        ).action_confirm()
        self.assertEqual(self.membership.period_ids, period)
        self.assertEqual(period.invoice_id.state, "posted")


class TestCancelDirect(MembershipTestCommon):
    """The importer's non-interactive cancellation path."""

    def _cancel_direct(self, membership, **vals):
        membership.action_cancel_direct(
            date_cancelled=date(date.today().year, 3, 1),
            date_end=date(date.today().year + 1, 12, 31),
            cancel_reason="left",
            **vals,
        )

    def test_from_draft_goes_through_waiting(self):
        membership = self._make_membership()
        self._cancel_direct(membership)
        self.assertEqual(membership.state, "cancelled")
        self.assertEqual(membership.cancel_reason, "left")

    def test_from_terminated_reopens_first(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        self._terminate(membership, cancel_reason="old")
        self._cancel_direct(membership)
        self.assertEqual(membership.state, "cancelled")
        self.assertEqual(membership.date_end, date(date.today().year + 1, 12, 31))

    def test_accepts_date_strings_from_rpc(self):
        # The importer sends ISO strings over XML-RPC, not date objects.
        membership = self._make_membership()
        membership.action_cancel_direct(
            date_cancelled="%s-03-01" % date.today().year,
            date_end="%s-12-31" % (date.today().year + 1),
            cancel_reason="rpc",
        )
        self.assertEqual(membership.state, "cancelled")
        self.assertEqual(membership.date_end, date(date.today().year + 1, 12, 31))

    def test_past_end_date_string_terminates(self):
        membership = self._make_membership(
            date_start=date(date.today().year - 1, 1, 1),
        )
        membership.action_cancel_direct(
            date_cancelled="%s-01-15" % (date.today().year - 1),
            date_end="%s-12-31" % (date.today().year - 1),
            cancel_reason="rpc",
        )
        self.assertEqual(membership.state, "terminated")

    def test_rerun_on_a_terminated_membership_only_corrects_it(self):
        membership = self._make_membership(date_start=date(date.today().year - 1, 1, 1))
        past_end = "%s-12-31" % (date.today().year - 1)
        membership.action_cancel_direct(date_end=past_end, cancel_reason="first")
        membership.action_cancel_direct(date_end=past_end, cancel_reason="corrected")
        self.assertEqual(membership.state, "terminated")
        self.assertEqual(membership.cancel_reason, "corrected")

    def test_rerun_updates_an_existing_cancellation(self):
        membership = self._make_membership()
        membership.action_submit()
        self._cancel_direct(membership)
        membership.action_cancel_direct(
            date_cancelled=date(date.today().year, 3, 1),
            date_end=date(date.today().year + 2, 6, 30),
            cancel_reason="moved",
        )
        self.assertEqual(membership.state, "cancelled")
        self.assertEqual(membership.date_end, date(date.today().year + 2, 6, 30))
        self.assertEqual(membership.cancel_reason, "moved")


class TestDraftMembershipGuard(MembershipTestCommon):
    def test_period_refused_on_a_draft_membership(self):
        membership = self._make_membership()
        with self.assertRaises(ValidationError):
            self.env["membership.period"].create({
                "membership_id": membership.id,
                "membership_year": date.today().year,
            })


class TestRenewalCandidates(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"
        self.target_year = date.today().year + 1

    def _run(self, dry_run=False):
        wizard = self.env["membership.renewal.wizard"].create({
            "target_year": self.target_year,
            "company_ids": [(6, 0, self.company.ids)],
            "dry_run": dry_run,
        })
        wizard.action_run()
        return wizard

    def _membership_in_state(self, name, state):
        membership = self._make_membership(
            partner_id=self.env["res.partner"].create({"name": name}).id,
        )
        membership.action_submit()
        if state in ("active", "cancelled"):
            membership._do_transition("active")
        if state == "cancelled":
            membership._schedule_termination(
                date_end=date(self.target_year, 12, 31),
            )
        return membership

    def test_active_renews_and_waiting_does_not(self):
        active = self._membership_in_state("Active", "active")
        waiting = self._membership_in_state("Waiting", "waiting")
        self._run()
        self.assertTrue(active.period_ids)
        self.assertFalse(waiting.period_ids)

    def test_cancelled_within_the_target_year_still_renews(self):
        cancelled = self._membership_in_state("Cancelled", "cancelled")
        self._run()
        self.assertTrue(cancelled.period_ids)

    def test_expired_cancellation_does_not_renew(self):
        membership = self._make_membership(
            partner_id=self.env["res.partner"].create({"name": "Expired"}).id,
            date_start=date(date.today().year - 1, 1, 1),
        )
        membership.action_submit()
        membership._do_transition("active")
        # Cancelled and already past its end date: the termination cron has not
        # caught up, but it must not be renewed.
        membership._do_transition(
            "cancelled", date_end=date(date.today().year - 1, 12, 31)
        )
        self.assertEqual(membership.state, "cancelled")
        self._run()
        self.assertFalse(membership.period_ids)

    def test_dry_run_writes_nothing_and_says_so(self):
        active = self._membership_in_state("Dry", "active")
        wizard = self._run(dry_run=True)
        self.assertFalse(active.period_ids)
        line = wizard.result_line_ids.filtered(
            lambda result: result.membership_id == active
        )
        self.assertIn("Would create", line.message)


class TestMemberEmailFollowers(MembershipTestCommon):
    def test_creator_does_not_follow_and_gets_no_copy(self):
        # Also covers a membership manager without accounting rights (manual mode).
        self.company.membership_invoicing_strategy = "manual"
        office = self.env["res.users"].create({
            "name": "Office",
            "login": "office@example.com",
            "email": "office@example.com",
            "groups_id": [(6, 0, [
                self.env.ref("base.group_user").id,
                self.env.ref("association_membership.group_membership_manager").id,
            ])],
        })
        membership = self.env["membership.membership"].with_user(office).create({
            "partner_id": self.partner.id,
            "company_id": self.company.id,
            "product_id": self.product.id,
        })
        membership.action_submit()
        self.env["membership.activate.wizard"].with_user(office).with_context(
            default_membership_id=membership.id
        ).create({}).action_confirm()
        self.assertNotIn(office.partner_id, membership.message_partner_ids)
        welcome = membership.message_ids.filtered(lambda m: self.partner in m.partner_ids)
        self.assertEqual(len(welcome), 1)
        self.assertNotIn(office.partner_id, welcome.notification_ids.res_partner_id)
        self.env["membership.cancel.wizard"].with_user(office).with_context(
            default_membership_id=membership.id
        ).create({
            "send_cancellation_message": True,
            "cancel_reason": "Left the association",
        }).action_confirm()
        self.assertEqual(membership.state, "cancelled")
        self.assertNotIn(office.partner_id, membership.message_partner_ids)


class TestMigration(MembershipTestCommon):
    def test_2_8_0_flags_products_in_membership_category(self):
        path = get_module_path("association_membership") + "/migrations/18.0.2.8.0/post-migrate.py"
        spec = importlib.util.spec_from_file_location("association_membership_2_8_0", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        category = self.env.ref("association_membership.product_category_membership")
        child = self.env["product.category"].create({"name": "Tiers", "parent_id": category.id})
        legacy = self.env["product.template"].create({"name": "Legacy", "categ_id": child.id})
        other = self.env["product.template"].create({"name": "Shirt"})
        migration._flag_membership_products(self.env)
        self.assertTrue(legacy.membership_ok)
        self.assertFalse(other.membership_ok)

    def _load_6_0_0_migration(self):
        path = get_module_path("association_membership") + "/migrations/18.0.6.0.0/post-migrate.py"
        spec = importlib.util.spec_from_file_location("association_membership_6_0_0", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration

    def _highest_own_counter(self):
        sequences = self.env["ir.sequence"].sudo().search([
            ("code", "=", "association.membership.number.seq"),
            ("company_id", "!=", False),
        ])
        return max(sequences.mapped("number_next_actual") or [0])

    def test_6_0_0_lifts_the_shared_counter_above_every_company_counter(self):
        # Companies counted on their own before this version; the shared counter
        # must not re-issue numbers those counters already handed out.
        migration = self._load_6_0_0_migration()
        self.company.member_number_own_sequence = True
        own = self.company._get_membership_number_sequence()
        shared = self.company._default_membership_number_sequence()
        target = max(self._highest_own_counter(), shared.number_next_actual) + 500
        own.sudo().write({"number_next": target})
        self.env.invalidate_all()

        migration._raise_shared_counter(self.env)
        self.env.invalidate_all()
        self.assertGreaterEqual(shared.number_next_actual, target)

    def test_6_0_0_migration_never_lowers_the_shared_counter(self):
        migration = self._load_6_0_0_migration()
        shared = self.company._default_membership_number_sequence()
        # Above every per-company counter, wherever they happen to stand.
        high = self._highest_own_counter() + 1000
        shared.sudo().write({"number_next": high})
        self.env.invalidate_all()

        migration._raise_shared_counter(self.env)
        migration._raise_shared_counter(self.env)
        self.env.invalidate_all()
        self.assertEqual(shared.number_next_actual, high)


class TestMigration630(MembershipTestCommon):
    def _load(self):
        path = get_module_path("association_membership") + "/migrations/18.0.6.3.0/post-migrate.py"
        spec = importlib.util.spec_from_file_location("association_membership_6_3_0", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        return migration

    def test_every_company_switches_to_the_default_numbering(self):
        """D31: own counters are archived; the default one moves above them."""
        self.company.member_number_own_sequence = True
        own = self.company._get_membership_number_sequence()
        default = self.company._default_membership_number_sequence()
        target = default.number_next_actual + 500
        own.write({"number_next": target})
        default.write({"prefix": "OLD/", "padding": 2})
        self._load()._switch_to_default_numbering(self.env)
        self.env.invalidate_all()
        self.assertFalse(own.active)
        self.assertEqual((default.prefix, default.padding), ("MEM/%(year)s/", 5))
        self.assertGreaterEqual(default.number_next_actual, target)

    def test_invoice_contacts_are_recomputed(self):
        organisation = self.env["res.partner"].create({"name": "Stale", "is_company": True})
        membership = self._make_membership(partner_id=organisation.id)
        billing = self.env["res.partner"].create(
            {"name": "Stale Billing", "type": "invoice", "parent_id": organisation.id}
        )
        # A value stored before the field was computed.
        self.env.cr.execute(
            "UPDATE membership_membership SET invoice_partner_id = %s WHERE id = %s",
            [organisation.id, membership.id],
        )
        self.env.invalidate_all()
        self._load()._recompute_contacts(self.env)
        self.env.invalidate_all()
        self.assertEqual(membership.invoice_partner_id, billing)


class TestAnnualReceiptWizard(MembershipTestCommon):
    def test_empty_run_explains_instead_of_raising(self):
        """15.4: no eligible period is not an "Invalid Operation"."""
        action = self.env["tax.receipt.annual.create"].create({
            "start_date": date(2000, 1, 1),
            "end_date": date(2000, 12, 31),
            "company_id": self.company.id,
        }).generate_annual_receipts()
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn(self.company.display_name, action["params"]["message"])
        self.assertFalse(
            self.env["donation.tax.receipt"].search([("donation_date", "=", date(2000, 12, 31))])
        )

    def test_receipt_links_to_its_periods(self):
        """15.27: the receipt form opens the periods it covers."""
        self.company.membership_invoicing_strategy = "manual"
        self.partner.tax_receipt_option = "annual"
        membership = self._make_membership()
        period = self._make_period(membership, amount=50.0)
        period.action_mark_as_paid()
        receipt = self._run_annual_wizard()
        self.assertEqual(receipt.membership_period_count, 1)
        action = receipt.action_view_membership_periods()
        self.assertEqual(self.env["membership.period"].search(action["domain"]), period)


class TestPaymentDate(MembershipTestCommon):
    """15.5: a fee belongs to the year the money came in."""

    def _pay(self, invoice, payment_date):
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=invoice.ids,
        ).create({"payment_date": payment_date}).action_create_payments()

    def _invoiced_period(self, invoice_date, option="annual"):
        self.partner.tax_receipt_option = option
        membership = self._make_membership()
        membership.invoicing_strategy = "confirm"
        period = self._make_period(membership, amount=50.0)
        period._apply_invoicing_strategy(invoice_date=invoice_date)
        return period

    def _annual_dict(self, year):
        receipt_dict = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict, date(year, 1, 1), date(year, 12, 31), self.company
        )
        return receipt_dict

    def test_invoice_paid_in_january_counts_for_that_year(self):
        year = date.today().year
        period = self._invoiced_period(date(year - 1, 12, 15))
        self._pay(period.invoice_id, date(year, 1, 10))
        self.assertEqual(period.invoice_id.payment_state, "paid")
        self.assertEqual(period.date_paid, date(year, 1, 10))
        self.assertIn(self.partner, self._annual_dict(year))
        self.assertNotIn(self.partner, self._annual_dict(year - 1))

    def test_per_payment_receipt_carries_the_payment_date(self):
        year = date.today().year
        period = self._invoiced_period(date(year - 1, 12, 15), option="each")
        self._pay(period.invoice_id, date(year, 1, 10))
        self.assertEqual(period.tax_receipt_id.donation_date, date(year, 1, 10))

    def test_unreconciling_clears_the_payment_date(self):
        period = self._invoiced_period(date.today())
        self._pay(period.invoice_id, date.today())
        self.assertTrue(period.date_paid)
        period.invoice_id.line_ids.remove_move_reconcile()
        self.assertFalse(period.date_paid)

    def test_migration_fills_payment_dates(self):
        period = self._invoiced_period(date.today())
        self._pay(period.invoice_id, date.today())
        period.date_paid = False
        path = get_module_path("association_membership") + "/migrations/18.0.6.4.0/post-migrate.py"
        spec = importlib.util.spec_from_file_location("association_membership_6_4_0", path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migration._fill_payment_dates(self.env)
        self.assertEqual(period.date_paid, date.today())


class TestAnnualReceiptDryRun(MembershipTestCommon):
    """15.3, D24, 15.34."""

    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"
        self.year = date.today().year

    def _paid_member(self, name):
        partner = self.env["res.partner"].create({"name": name, "tax_receipt_option": "annual"})
        period = self._make_period(self._make_membership(partner_id=partner.id), amount=50.0)
        period.action_mark_as_paid()
        return partner, period

    def _wizard(self, **vals):
        return self.env["tax.receipt.annual.create"].create({
            "start_date": date(self.year, 1, 1),
            "end_date": date(self.year, 12, 31),
            "company_id": self.company.id,
            **vals,
        })

    def test_receipt_is_dated_today_and_covers_the_range(self):
        self._paid_member("Anna")
        receipt = self._run_annual_wizard()
        self.assertEqual(receipt.date, date.today())
        self.assertEqual(receipt.donation_date, date(self.year, 12, 31))

    def test_preview_shows_what_would_be_created(self):
        _anna, anna_period = self._paid_member("Anna")
        _ben, ben_period = self._paid_member("Ben")
        wizard = self._wizard()
        self.assertEqual(wizard.preview_donor_count, 2)
        self.assertEqual(wizard.preview_period_count, 2)
        self.assertEqual(wizard.preview_amount, 100.0)
        action = wizard.action_preview()
        self.assertEqual(
            self.env["membership.period"].search(action["domain"]), anna_period | ben_period
        )
        self.assertFalse(self.env["donation.tax.receipt"].search([("partner_id.name", "in", ["Anna", "Ben"])]))

    def test_only_the_chosen_donors(self):
        anna, _anna_period = self._paid_member("Anna")
        _ben, ben_period = self._paid_member("Ben")
        wizard = self._wizard(partner_ids=[(6, 0, anna.ids)])
        self.assertEqual(wizard.preview_donor_count, 1)
        receipts = self.env["donation.tax.receipt"].search(wizard.generate_annual_receipts()["domain"])
        self.assertEqual(receipts.partner_id, anna)
        self.assertFalse(ben_period.tax_receipt_id)

    def test_donor_with_a_receipt_is_skipped_not_aborting(self):
        anna, anna_period = self._paid_member("Anna")
        ben, ben_period = self._paid_member("Ben")
        existing = self.env["donation.tax.receipt"].create({
            "partner_id": anna.id,
            "amount": 10.0,
            "type": "annual",
            "donation_date": date(self.year, 12, 31),
        })
        wizard = self._wizard()
        self.assertEqual(wizard.skipped_receipt_ids, existing)
        self.assertEqual(wizard.preview_donor_count, 1)
        action = wizard.generate_annual_receipts()
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn(existing.number, action["params"]["message"])
        created = self.env["donation.tax.receipt"].search(action["params"]["next"]["domain"])
        self.assertEqual(created.partner_id, ben)
        self.assertEqual(ben_period.tax_receipt_id, created)
        self.assertFalse(anna_period.tax_receipt_id)

    def test_only_skipped_donors_explains_the_empty_run(self):
        anna, _period = self._paid_member("Anna")
        self.env["donation.tax.receipt"].create({
            "partner_id": anna.id,
            "amount": 10.0,
            "type": "annual",
            "donation_date": date(self.year, 12, 31),
        })
        action = self._wizard().generate_annual_receipts()
        self.assertEqual(action["tag"], "display_notification")
        self.assertIn("Anna", action["params"]["message"])
        self.assertNotIn("next", action["params"])


class TestSendAndPrintReceipts(MembershipTestCommon):
    """15.2: bulk send with the company's template; print from the menu."""

    def _receipt(self, partner=None, company=None):
        return self.env["donation.tax.receipt"].create({
            "partner_id": (partner or self.partner).id,
            "company_id": (company or self.company).id,
            "amount": 50.0,
            "type": "each",
            "donation_date": date.today(),
        })

    def test_several_receipts_are_sent_one_email_each(self):
        other = self.env["res.partner"].create({"name": "Other", "email": "other@example.com"})
        receipts = self._receipt() | self._receipt(partner=other)
        action = receipts.action_send_tax_receipt()
        self.assertEqual(action["context"]["default_res_ids"], receipts.ids)
        self.assertEqual(action["context"]["default_composition_mode"], "comment")
        self.assertEqual(
            action["context"]["default_template_id"],
            self.env.ref("donation_base.tax_receipt_email_template").id,
        )
        composer = self.env["mail.compose.message"].with_context(**action["context"]).create({})
        composer._action_send_mail()
        for receipt in receipts:
            message = receipt.message_ids.filtered(lambda m: m.message_type == "comment")
            self.assertEqual(len(message), 1)
            self.assertEqual(message.partner_ids, receipt.partner_id)
            self.assertEqual(len(message.attachment_ids), 1)

    def test_company_template_is_used(self):
        template = self.env.ref("donation_base.tax_receipt_email_template").copy({"name": "Ours"})
        self.company.membership_tax_receipt_template_id = template
        action = self._receipt().action_send_tax_receipt()
        self.assertEqual(action["context"]["default_template_id"], template.id)

    def test_template_must_be_for_receipts(self):
        with self.assertRaises(ValidationError):
            self.company.membership_tax_receipt_template_id = self.env.ref(
                "association_membership.mail_template_membership_welcome"
            )

    def test_missing_email_and_mixed_companies_are_refused(self):
        silent = self.env["res.partner"].create({"name": "Silent"})
        with self.assertRaises(UserError):
            (self._receipt() | self._receipt(partner=silent)).action_send_tax_receipt()
        branch = self.env["res.company"].create({"name": "Branch", "parent_id": self.company.id})
        with self.assertRaises(UserError):
            (self._receipt() | self._receipt(company=branch)).action_send_tax_receipt()

    def test_send_is_offered_in_the_list(self):
        action = self.env.ref("association_membership.action_send_tax_receipts")
        self.assertEqual(action.binding_model_id.model, "donation.tax.receipt")
        self.assertEqual(action.binding_view_types, "list")

    def test_print_wizard_prints_the_company_report_and_is_in_the_menu(self):
        # Without a document layout Odoo opens the layout configurator instead.
        self.company.external_report_layout_id = self.env.ref("web.external_layout_standard")
        receipt = self._receipt()
        wizard = self.env["donation.tax.receipt.print"].create({"receipt_ids": [(6, 0, receipt.ids)]})
        action = wizard.print_receipts()
        self.assertEqual(action["report_name"], self.company._get_membership_tax_receipt_report().report_name)
        self.assertEqual(receipt.print_date, date.today())
        menu = self.env.ref("association_membership.menu_membership_tax_receipts_print")
        self.assertEqual(menu.action, self.env.ref("donation_base.donation_tax_receipt_print_action"))


class TestRenewalCron(MembershipTestCommon):
    def test_cron_renews_for_next_year(self):
        """15.20: without the offset setting the job targets next year."""
        self.company.membership_invoicing_strategy = "manual"
        membership = self._make_membership()
        membership.action_activate_direct()
        self.env["membership.membership"].cron_generate_membership_renewals()
        self.assertIn(date.today().year + 1, membership.period_ids.mapped("membership_year"))


class TestMembershipProductDefaults(MembershipTestCommon):
    def test_new_membership_product_is_a_sellable_service(self):
        """15.18: the Membership Products menu creates services for sale only."""
        action = self.env.ref("association_membership.action_membership_products")
        self.assertEqual(action.res_model, "product.template")
        context = safe_eval(action.context)
        product = self.env["product.template"].with_context(**context).create({"name": "New Type"})
        self.assertEqual(product.type, "service")
        self.assertTrue(product.sale_ok)
        self.assertFalse(product.purchase_ok)
        self.assertTrue(product.membership_ok)

    def test_tiers_are_managed_on_the_template(self):
        """15.19: tiers are variants; every internal user may edit them."""
        self.assertIn(
            self.env.ref("product.group_product_variant"),
            self.env.ref("base.group_user").implied_ids,
        )
        action = self.env.ref("association_membership.action_membership_product_tiers")
        self.assertEqual(action.res_model, "product.product")
        self.assertEqual(
            action.view_ids.filtered(lambda v: v.view_mode == "list").view_id,
            self.env.ref("association_membership.view_product_membership_list"),
        )
