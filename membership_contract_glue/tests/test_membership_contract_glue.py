from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestMembershipContractGlue(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Member A"})
        cls.other_partner = cls.env["res.partner"].create({"name": "Region A", "is_company": True})
        cls.invoice_partner = cls.env["res.partner"].create(
            {
                "name": "Invoice A",
                "parent_id": cls.partner.id,
                "type": "invoice",
            }
        )
        cls.membership_product = cls.env["product.product"].create(
            {
                "name": "Membership Gold",
                "type": "service",
                "membership": True,
                "membership_date_from": "2026-01-01",
                "membership_date_to": "2026-12-31",
                "list_price": 120.0,
            }
        )

    def _create_membership_line(self, **overrides):
        values = {
            "membership_id": self.membership_product.id,
            "member_price": 120.0,
            "date": "2026-01-01",
            "date_from": "2026-01-01",
            "date_to": "2026-12-31",
            "partner": self.partner.id,
            "state": "waiting",
        }
        values.update(overrides)
        return self.env["membership.membership_line"].create(values)

    def _get_sale_journal(self):
        journal = self.env["account.journal"].search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", self.env.company.id),
            ],
            limit=1,
        )
        if journal:
            return journal
        return self.env["account.journal"].create(
            {
                "name": "Sales",
                "code": "SAL",
                "type": "sale",
                "company_id": self.env.company.id,
            }
        )

    def test_action_create_membership_contract_defaults_enabled(self):
        self.env.company.membership_contract_yearly_defaults = True
        action = self.env.ref(
            "membership_contract_glue.action_create_membership_contract_from_partner"
        ).read()[0]
        ctx = action["context"]

        self.assertEqual(action["res_model"], "contract.contract")
        self.assertEqual(action["target"], "new")
        self.assertIn("'default_is_membership_contract': True", ctx)
        self.assertIn("'default_contract_type': 'sale'", ctx)
        self.assertIn("'create_membership_contract_from_partner': True", ctx)

        contract_model = self.env["contract.contract"].with_context(
            default_partner_id=self.partner.id,
            default_invoice_partner_id=self.partner.id,
            create_membership_contract_from_partner=True,
        )
        defaults = contract_model.default_get(
            [
                "name",
                "company_id",
                "partner_id",
                "invoice_partner_id",
                "contract_type",
                "is_membership_contract",
                "line_recurrence",
                "recurring_interval",
                "recurring_rule_type",
            ]
        )

        self.assertEqual(defaults["name"], f"{self.partner.display_name} - {self.env.company.display_name}")
        self.assertEqual(defaults["company_id"], self.env.company.id)
        self.assertEqual(defaults["contract_type"], "sale")
        self.assertTrue(defaults["is_membership_contract"])
        self.assertEqual(defaults["recurring_interval"], 1)
        self.assertEqual(defaults["recurring_rule_type"], "yearly")
        self.assertFalse(defaults["line_recurrence"])

    def test_action_create_membership_contract_defaults_disabled(self):
        self.env.company.membership_contract_yearly_defaults = False
        defaults = (
            self.env["contract.contract"]
            .with_context(
                default_partner_id=self.partner.id,
                default_invoice_partner_id=self.partner.id,
                create_membership_contract_from_partner=True,
            )
            .default_get(
                [
                    "recurring_interval",
                    "recurring_rule_type",
                    "recurring_next_date",
                ]
            )
        )

        self.assertEqual(defaults["recurring_interval"], 1)
        self.assertEqual(defaults["recurring_rule_type"], "monthly")
        self.assertNotIn("recurring_next_date", defaults)

    def test_prepare_invoice_sets_delegated_member_for_invoice_child(self):
        journal = self._get_sale_journal()
        contract = self.env["contract.contract"].new(
            {
                "name": "Contract A",
                "code": "C-0001",
                "company_id": self.env.company.id,
                "partner_id": self.partner.id,
                "invoice_partner_id": self.invoice_partner.id,
                "contract_type": "sale",
                "currency_id": self.env.company.currency_id.id,
                "journal_id": journal.id,
                "is_membership_contract": True,
            }
        )

        invoice_vals = contract._prepare_invoice(
            fields.Date.context_today(contract), journal=journal
        )

        self.assertEqual(invoice_vals["partner_id"], self.invoice_partner.id)
        self.assertEqual(invoice_vals["delegated_member_id"], self.partner.id)

    def test_prepare_invoice_keeps_non_delegated_member_unset(self):
        journal = self._get_sale_journal()
        contract = self.env["contract.contract"].new(
            {
                "name": "Contract B",
                "code": "C-0002",
                "company_id": self.env.company.id,
                "partner_id": self.partner.id,
                "invoice_partner_id": self.partner.id,
                "contract_type": "sale",
                "currency_id": self.env.company.currency_id.id,
                "journal_id": journal.id,
                "is_membership_contract": True,
            }
        )

        invoice_vals = contract._prepare_invoice(
            fields.Date.context_today(contract), journal=journal
        )

        self.assertEqual(invoice_vals["partner_id"], self.partner.id)
        self.assertNotIn("delegated_member_id", invoice_vals)

    def test_membership_line_requires_start_and_end_dates(self):
        with self.assertRaises(ValidationError):
            self._create_membership_line(date_to=False)

        with self.assertRaises(ValidationError):
            self._create_membership_line(date_from=False)

    def test_membership_state_change_posts_partner_chatter(self):
        line = self._create_membership_line()
        message_ids_before = set(self.partner.message_ids.ids)

        line.write({"state": "invoiced"})

        new_messages = self.partner.message_ids.filtered(
            lambda message: message.id not in message_ids_before
        )
        self.assertTrue(new_messages)
        self.assertTrue(
            any("Membership status changed" in (message.body or "") for message in new_messages)
        )

    def test_membership_timeline_includes_memberships_and_invoices(self):
        self._create_membership_line()
        journal = self._get_sale_journal()
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "journal_id": journal.id,
                "partner_id": self.partner.id,
                "invoice_date": "2026-01-15",
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": "Membership 2026",
                            "quantity": 1,
                            "price_unit": 120.0,
                        }
                    )
                ],
            }
        )

        self.assertIn("Membership Gold", self.partner.membership_timeline_html)
        self.assertIn("Invoice:", self.partner.membership_timeline_html)
        self.assertIn(str(invoice.amount_total), self.partner.membership_timeline_html)

    def test_relationship_summary_splits_current_and_past_relations(self):
        relation_type = self.env["res.partner.relation.type"].create(
            {
                "name": "Regional Association",
                "name_inverse": "Regional Member",
                "contact_type_left": "p",
                "contact_type_right": "c",
            }
        )
        self.env["res.partner.relation"].create(
            {
                "left_partner_id": self.partner.id,
                "right_partner_id": self.other_partner.id,
                "type_id": relation_type.id,
                "date_start": "2026-01-01",
            }
        )
        self.env["res.partner.relation"].create(
            {
                "left_partner_id": self.partner.id,
                "right_partner_id": self.other_partner.id,
                "type_id": relation_type.id,
                "date_start": "2025-01-01",
                "date_end": "2025-12-31",
            }
        )

        self.assertIn("Current Relationships", self.partner.relationship_summary_html)
        self.assertIn("Past Relationships", self.partner.relationship_summary_html)
        self.assertIn("Region A", self.partner.relationship_summary_html)

    def test_silent_import_creates_draft_invoice_without_manual_contract_message(self):
        journal = self._get_sale_journal()
        start_date = fields.Date.context_today(self.env["contract.contract"])

        contract = (
            self.env["contract.contract"]
            .with_context(
                create_membership_contract_from_partner=True,
                auto_invoice_membership_contract_on_save=True,
                silent_membership_contract_import=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
                mail_auto_subscribe_no_notify=True,
                mail_notify_force_send=False,
                mail_notify_noemail=True,
                tracking_disable=True,
            )
            .create(
                {
                    "name": "Silent Import Contract",
                    "partner_id": self.partner.id,
                    "invoice_partner_id": self.invoice_partner.id,
                    "company_id": self.env.company.id,
                    "contract_type": "sale",
                    "journal_id": journal.id,
                    "pricelist_id": self.partner.property_product_pricelist.id,
                    "is_membership_contract": True,
                    "line_recurrence": False,
                    "recurring_rule_type": "yearly",
                    "recurring_interval": 1,
                    "recurring_invoicing_type": "pre-paid",
                    "date_start": start_date,
                    "contract_line_ids": [
                        Command.create(
                            {
                                "product_id": self.membership_product.id,
                                "name": "Membership",
                                "quantity": 1,
                                "uom_id": self.membership_product.uom_id.id,
                                "price_unit": 120.0,
                                "automatic_price": False,
                                "date_start": start_date,
                                "recurring_next_date": start_date,
                                "recurring_rule_type": "yearly",
                                "recurring_interval": 1,
                                "recurring_invoicing_type": "pre-paid",
                            }
                        )
                    ],
                }
            )
        )

        invoices = contract._get_related_invoices()

        self.assertEqual(len(invoices), 1)
        self.assertEqual(invoices.state, "draft")
        self.assertEqual(invoices.partner_id, self.invoice_partner)
        self.assertEqual(invoices.delegated_member_id, self.partner)
        self.assertFalse(
            any("Contract manually invoiced" in (message.body or "") for message in contract.message_ids)
        )
