import importlib.util
from datetime import date

from odoo.exceptions import UserError, ValidationError
from odoo.modules.module import get_module_path
from odoo.tests import TransactionCase


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

    def _make_contribution(self, membership, **overrides):
        vals = {
            "membership_id": membership.id,
            "membership_year": date.today().year,
        }
        vals.update(overrides)
        return self.env["membership.contribution"].create(vals)

    def _run_annual_wizard(self):
        action = self.env["tax.receipt.annual.create"].create({
            "start_date": date(date.today().year, 1, 1),
            "end_date": date(date.today().year, 12, 31),
            "company_id": self.company.id,
        }).generate_annual_receipts()
        return self.env["donation.tax.receipt"].search(action["domain"])

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
        membership._do_transition("terminated")
        self.assertEqual(membership.state, "terminated")
        with self.assertRaises(UserError):
            membership._do_transition("active")

    def test_activate_direct_from_every_state(self):
        for start_state in ("draft", "waiting", "cancelled", "terminated"):
            membership = self._make_membership(
                partner_id=self.env["res.partner"].create({"name": start_state}).id,
            )
            if start_state != "draft":
                membership.action_submit()
            if start_state in ("cancelled", "terminated"):
                membership._do_transition("active")
                membership._do_transition(start_state, cancel_reason="left")
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
            membership._do_transition(end_state, cancel_reason="left")
            membership.action_reopen_waiting()
            self.assertEqual(membership.state, "waiting")
            self.assertFalse(membership.date_cancelled)
            self.assertFalse(membership.date_end)
            self.assertFalse(membership.cancel_reason)

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

    def test_unlink_blocked_with_contributions(self):
        membership = self._make_membership()
        self._make_contribution(membership)
        with self.assertRaises(UserError):
            membership.unlink()

    def test_revert_to_draft_blocked_with_contributions(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._do_transition("active")
        self._make_contribution(membership)
        membership._do_transition("terminated")
        with self.assertRaises(UserError):
            membership.action_revert_to_draft()
        self.assertEqual(membership.state, "terminated")

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
        contribution = self._make_contribution(membership)
        self.assertFalse(contribution.is_free)
        self.assertEqual(contribution.billing_status, "to_invoice")


class TestMembershipTiers(MembershipTestCommon):
    def test_tier_change_keeps_past_contributions(self):
        membership = self._make_membership(product_id=self.tier_small.id)
        contribution = self._make_contribution(membership)
        membership.product_id = self.tier_large
        self.assertEqual(membership.amount, 200.0)
        self.assertEqual(contribution.product_id, self.tier_small)
        self.assertEqual(contribution.amount, 100.0)
        next_contribution = self._make_contribution(
            membership, membership_year=date.today().year + 1
        )
        self.assertEqual(next_contribution.product_id, self.tier_large)
        self.assertEqual(next_contribution.amount, 200.0)

    def test_parallel_membership_of_same_type_rejected(self):
        self._make_membership(product_id=self.tier_small.id)
        with self.assertRaises(ValidationError):
            self._make_membership(product_id=self.tier_large.id)

    def test_type_change_rejected_once_contributions_exist(self):
        membership = self._make_membership(product_id=self.tier_small.id)
        membership.product_id = self.product
        membership.product_id = self.tier_small
        self._make_contribution(membership)
        with self.assertRaises(ValidationError):
            membership.product_id = self.product


class TestContributionBilling(MembershipTestCommon):
    def test_amount_defaults_from_membership_amount(self):
        membership = self._make_membership()
        membership.amount = 99.0
        contribution = self._make_contribution(membership)
        self.assertEqual(contribution.amount, 99.0)

    def test_zero_amount_is_free_and_waived(self):
        contribution = self._make_contribution(self._make_membership(), amount=0.0)
        self.assertTrue(contribution.is_free)
        self.assertEqual(contribution.billing_status, "waived")

    def test_nonzero_amount_with_no_invoice_is_to_invoice(self):
        contribution = self._make_contribution(self._make_membership(), amount=50.0)
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


class TestManualInvoicing(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"
        self.membership = self._make_membership()

    def test_new_contribution_is_to_invoice(self):
        contribution = self._make_contribution(self.membership)
        self.assertEqual(contribution.membership_invoicing_strategy, "manual")
        self.assertEqual(contribution.billing_status, "to_invoice")

    def test_free_contribution_is_waived(self):
        contribution = self._make_contribution(self.membership, amount=0.0)
        self.assertEqual(contribution.billing_status, "waived")

    def test_mark_as_paid(self):
        contribution = self._make_contribution(self.membership)
        contribution.action_mark_as_paid()
        self.assertEqual(contribution.billing_status, "paid")
        self.assertEqual(contribution.amount_paid, contribution.amount)

    def test_strategy_is_frozen_at_creation(self):
        contribution = self._make_contribution(self.membership)
        contribution.action_mark_as_paid()
        self.company.membership_invoicing_strategy = "draft"
        self.assertEqual(contribution.membership_invoicing_strategy, "manual")
        self.assertEqual(contribution.billing_status, "paid")


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
        contribution, invoice = self._make_paid_invoice()
        receipt = contribution.tax_receipt_id
        self.assertFalse(receipt.activity_ids)
        refund = invoice._reverse_moves()
        refund.action_post()
        self.assertEqual(len(receipt.activity_ids), 1)
        self.assertTrue(receipt.exists())

    def test_unreconciled_payment_flags_receipt(self):
        contribution, invoice = self._make_paid_invoice()
        receipt = contribution.tax_receipt_id
        invoice.line_ids.remove_move_reconcile()
        self.assertNotEqual(invoice.payment_state, "paid")
        self.assertEqual(len(receipt.activity_ids), 1)


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

    def test_annual_wizard_links_contributions_once(self):
        self.partner.tax_receipt_option = "annual"
        membership = self._make_membership()
        contribution = self._make_contribution(membership, amount=50.0)
        contribution._apply_invoicing_strategy(strategy="confirm")
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=contribution.invoice_id.ids,
        ).create({}).action_create_payments()
        receipt = self._run_annual_wizard()
        self.assertEqual(receipt.membership_contribution_ids, contribution)
        self.assertEqual(contribution.tax_receipt_id, receipt)
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

    def _paid_contribution(self, option, partner=None):
        partner = partner or self.env["res.partner"].create({"name": option})
        partner.tax_receipt_option = option
        membership = self._make_membership(partner_id=partner.id)
        contribution = self._make_contribution(membership, amount=50.0)
        contribution.action_mark_as_paid()
        return contribution

    def test_mark_as_paid_sets_payment_date_and_issues_no_receipt(self):
        contribution = self._paid_contribution("each")
        self.assertEqual(contribution.date_paid, date.today())
        self.assertFalse(contribution.tax_receipt_id)

    def test_annual_receipt_covers_each_and_annual_partners(self):
        annual = self._paid_contribution("annual")
        each = self._paid_contribution("each")
        none = self._paid_contribution("none")
        receipts = self._run_annual_wizard()
        self.assertEqual(receipts.membership_contribution_ids, annual | each)
        self.assertEqual(set(receipts.mapped("type")), {"annual"})
        self.assertFalse(none.tax_receipt_id)

    def test_imported_paid_history_is_not_receipted(self):
        partner = self.env["res.partner"].create({"name": "Imported", "tax_receipt_option": "annual"})
        membership = self._make_membership(partner_id=partner.id)
        # The importer writes the status directly, without a payment date.
        self._make_contribution(membership, amount=50.0).write(
            {"billing_status": "paid", "amount_paid": 50.0}
        )
        receipt_dict = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict, date(date.today().year, 1, 1), date(date.today().year, 12, 31), self.company
        )
        self.assertNotIn(partner, receipt_dict)

    def test_product_not_eligible_is_not_receipted(self):
        self.product.tax_receipt_ok = False
        contribution = self._paid_contribution("annual")
        receipt_dict = {}
        self.env["donation.tax.receipt"].update_tax_receipt_annual_dict(
            receipt_dict, date(date.today().year, 1, 1), date(date.today().year, 12, 31), self.company
        )
        self.assertNotIn(contribution._tax_receipt_partner(), receipt_dict)


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

    def test_cancel_from_waiting(self):
        membership = self._make_membership()
        membership.action_submit()
        year_end = date(date.today().year, 12, 31)
        membership._do_transition("cancelled", date_end=year_end)
        self.assertEqual(membership.state, "cancelled")
        self.assertTrue(membership.date_cancelled)
        self.assertEqual(membership.date_end, year_end)

    def test_cancel_from_waiting_terminates_when_end_date_past(self):
        membership = self._make_membership()
        membership.action_submit()
        membership._schedule_termination(
            date_cancelled=date.today(),
            date_end=date.today(),
        )
        self.assertEqual(membership.state, "terminated")

    def test_revert_to_draft_from_cancelled_clears_cancel_fields(self):
        membership = self._make_membership()
        membership.action_submit()
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
            .create({})
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
        invoice_partner = self.env["res.partner"].create({"name": "Invoice Contact"})
        membership = self._make_membership(invoice_partner_id=invoice_partner.id)
        membership.action_submit()
        activate, cancel = self._make_wizards(membership)
        self.assertFalse(activate.invoice_partner_included)
        activate.action_add_invoice_partner()
        self.assertIn(invoice_partner, activate.mail_partner_ids)
        self.assertTrue(activate.invoice_partner_included)
        cancel.action_add_invoice_partner()
        self.assertIn(invoice_partner, cancel.mail_partner_ids)

    def test_add_invoice_partner_is_idempotent(self):
        invoice_partner = self.env["res.partner"].create({"name": "Invoice Contact"})
        membership = self._make_membership(invoice_partner_id=invoice_partner.id)
        membership.action_submit()
        activate, _cancel = self._make_wizards(membership)
        activate.action_add_invoice_partner()
        activate.action_add_invoice_partner()
        self.assertEqual(len(activate.mail_partner_ids), 2)


class TestMembershipContributionYear(MembershipTestCommon):
    def _set_override(self, value):
        self.company.membership_default_contribution_year = value

    def test_zero_means_current_year(self):
        self._set_override(0)
        self.assertEqual(self.company._membership_contribution_year(), date.today().year)

    def test_past_override_falls_back_to_current_year(self):
        self._set_override(date.today().year - 1)
        self.assertEqual(self.company._membership_contribution_year(), date.today().year)

    def test_future_override_wins(self):
        self._set_override(date.today().year + 1)
        self.assertEqual(self.company._membership_contribution_year(), date.today().year + 1)

    def test_membership_default_year_uses_override(self):
        self._set_override(date.today().year + 1)
        membership = self._make_membership()
        self.assertEqual(membership._default_contribution_year(), date.today().year + 1)

    def test_contribution_default_year_follows_override(self):
        self._set_override(date.today().year + 1)
        membership = self._make_membership()
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "amount": 50.0,
        })
        self.assertEqual(contribution.membership_year, date.today().year + 1)

    def test_settings_year_text_can_be_cleared(self):
        self.company.membership_default_contribution_year = date.today().year + 2
        settings = self.env["res.config.settings"].create({})
        settings.membership_default_contribution_year_text = False
        self.assertEqual(self.company.membership_default_contribution_year, 0)


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
        contribution = self._make_contribution(membership, amount=50.0)
        contribution._apply_invoicing_strategy(strategy="draft")
        invoice = contribution.invoice_id
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
            ["state", "contribution_ids"], ["string", "selection"]
        )
        self.assertEqual(fields_de["contribution_ids"]["string"], "Beiträge")
        self.assertIn(("cancelled", "Gekündigt"), fields_de["state"]["selection"])

    def test_activation_invoice_template_renders(self):
        membership = self._make_membership()
        contribution = self.env["membership.contribution"].create({
            "membership_id": membership.id,
            "membership_year": date.today().year,
            "amount": 50.0,
        })
        contribution._apply_invoicing_strategy(strategy="draft")
        invoice = contribution.invoice_id
        template = self.env.ref("association_membership.mail_template_membership_activation_invoice")
        rendered = template._render_field("body_html", invoice.ids)[invoice.id]
        self.assertIn(self.partner.name, rendered)

    def test_settings_member_number_preview(self):
        self.company.member_number_prefix = "MEM/%(year)s/"
        self.company.member_number_padding = 5
        sequence = self.company._get_membership_number_sequence()
        settings = self.env["res.config.settings"].create({})
        expected = "MEM/%d/%s" % (
            date.today().year,
            str(sequence.number_next_actual).zfill(5),
        )
        self.assertEqual(settings.member_number_preview, expected)

    def test_lazy_sequence_uses_company_padding(self):
        sequence = self.company._get_membership_number_sequence()
        sequence.sudo().unlink()
        self.company.member_number_padding = 7
        sequence = self.company._get_membership_number_sequence()
        self.assertEqual(sequence.padding, 7)


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
        self.assertFalse(membership.contribution_ids)


class TestPaymentHooks(MembershipTestCommon):
    def test_payment_activates_and_issues_receipt_once(self):
        self.company.membership_auto_activate_on_payment = True
        self.partner.tax_receipt_option = "each"
        membership = self._make_membership()
        membership.action_submit()
        contribution = self._make_contribution(membership, amount=50.0)
        contribution._apply_invoicing_strategy(strategy="confirm")
        invoice = contribution.invoice_id
        self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=invoice.ids,
        ).create({}).action_create_payments()
        self.assertEqual(membership.state, "active")
        receipt = contribution.tax_receipt_id
        self.assertTrue(receipt)
        message_count = len(membership.message_ids)
        invoice._invoice_paid_hook()
        self.assertEqual(contribution.tax_receipt_id, receipt)
        self.assertEqual(len(membership.message_ids), message_count)

    def test_posting_a_refund_posts_one_review_message(self):
        membership = self._make_membership()
        contribution = self._make_contribution(membership, amount=50.0)
        contribution._apply_invoicing_strategy(strategy="confirm")
        refund = contribution.invoice_id._reverse_moves()
        refund.invoice_line_ids.membership_contribution_id = contribution
        refund.action_post()
        messages = membership.message_ids.filtered(lambda m: "A refund was posted" in (m.body or ""))
        self.assertEqual(len(messages), 1)


class TestReactivation(MembershipTestCommon):
    def test_welcome_message_unticked_when_already_sent(self):
        membership = self._make_membership()
        membership.action_submit()
        wizard = self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({})
        self.assertTrue(wizard.send_welcome_message)
        membership.date_welcome_sent = date.today()
        wizard = self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({})
        self.assertFalse(wizard.send_welcome_message)


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

    def test_wizards_use_the_setting(self):
        self.company.membership_company_mail_recipients = "invoice_contact"
        membership = self._make_membership(partner_id=self.organisation.id)
        membership.action_submit()
        for model in ("membership.activate.wizard", "membership.cancel.wizard"):
            wizard = self.env[model].with_context(default_membership_id=membership.id).create({})
            self.assertEqual(wizard.mail_partner_ids, self.billing)


class TestActivationContribution(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"

    def _activation_wizard(self, membership):
        return self.env["membership.activate.wizard"].with_context(
            default_membership_id=membership.id
        ).create({"send_welcome_message": False})

    def test_creates_contribution_when_missing(self):
        membership = self._make_membership()
        membership.action_submit()
        wizard = self._activation_wizard(membership)
        self.assertTrue(wizard.create_contribution)
        wizard.action_confirm()
        self.assertEqual(membership.state, "active")
        self.assertEqual(len(membership.contribution_ids), 1)
        self.assertEqual(membership.contribution_ids.billing_status, "to_invoice")
        self.assertFalse(membership.contribution_ids.invoice_id)

    def test_keeps_existing_contribution(self):
        membership = self._make_membership()
        membership.action_submit()
        self._make_contribution(membership, membership_year=membership._default_contribution_year())
        wizard = self._activation_wizard(membership)
        self.assertTrue(wizard.has_contribution)
        self.assertFalse(wizard.create_contribution)
        wizard.action_confirm()
        self.assertEqual(len(membership.contribution_ids), 1)


class TestNewMembershipWizard(MembershipTestCommon):
    def setUp(self):
        super().setUp()
        self.company.membership_invoicing_strategy = "manual"

    def _wizard(self, **vals):
        return self.env["membership.new.wizard"].create({
            "partner_id": self.partner.id,
            "product_id": self.tier_small.id,
            **vals,
        })

    def test_previews(self):
        wizard = self._wizard()
        self.assertEqual(wizard.amount, 100.0)
        self.assertEqual(wizard.mail_partner_ids, self.partner)
        self.assertTrue(wizard.send_welcome_message)
        number = wizard.membership_number_preview
        wizard.send_welcome_message = False
        action = wizard.action_confirm()
        membership = self.env["membership.membership"].browse(action["res_id"])
        self.assertEqual(membership.membership_number, number)

    def test_create_activate_contribute_and_welcome(self):
        action = self._wizard().action_confirm()
        membership = self.env["membership.membership"].browse(action["res_id"])
        self.assertEqual(membership.state, "active")
        self.assertEqual(membership.amount, 100.0)
        self.assertEqual(membership.contribution_ids.billing_status, "to_invoice")
        self.assertEqual(membership.contribution_ids.amount, 100.0)
        self.assertTrue(membership.date_welcome_sent)
        welcome = membership.message_ids.filtered(lambda m: m.partner_ids == self.partner)
        self.assertEqual(len(welcome), 1)

    def test_without_activation_stays_waiting(self):
        action = self._wizard(activate=False).action_confirm()
        membership = self.env["membership.membership"].browse(action["res_id"])
        self.assertEqual(membership.state, "waiting")
        self.assertFalse(membership.contribution_ids)
        self.assertFalse(membership.date_welcome_sent)

    def test_started_from_partner(self):
        action = self.partner.action_create_membership()
        self.assertEqual(action["res_model"], "membership.new.wizard")
        self.assertEqual(action["context"]["default_partner_id"], self.partner.id)


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
        wizard = self.env["membership.new.wizard"].with_user(office).create({
            "partner_id": self.partner.id,
            "product_id": self.product.id,
        })
        membership = self.env["membership.membership"].browse(wizard.action_confirm()["res_id"])
        self.assertNotIn(office.partner_id, membership.message_partner_ids)
        welcome = membership.message_ids.filtered(lambda m: self.partner in m.partner_ids)
        self.assertEqual(len(welcome), 1)
        self.assertNotIn(office.partner_id, welcome.notification_ids.res_partner_id)
        self.env["membership.cancel.wizard"].with_user(office).with_context(
            default_membership_id=membership.id
        ).create({"send_cancellation_message": True}).action_confirm()
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
