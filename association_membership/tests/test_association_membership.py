
import base64
import csv
import io
import re
from datetime import timedelta
from unittest.mock import patch

from psycopg2 import IntegrityError

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import TransactionCase


class TestAssociationMembership(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.today = fields.Date.today()
        cls.next_year = cls.today.year + 1

        cls.membership_category = cls.env["product.category"].create(
            {"name": "Membership Test Category"}
        )
        cls.env.company.write(
            {
                "membership_product_category_id": cls.membership_category.id,
                "membership_auto_activate_on_payment": False,
                "membership_cron_year_offset": 1,
                "membership_cron_auto_post": False,
                "member_number_prefix": "MEM/%(year)s/",
                "member_number_padding": 5,
            }
        )

        cls.receivable_account = cls.env["account.account"].create(
            {
                "name": "Membership test receivable",
                "code": "MTR",
                "account_type": "asset_receivable",
                "reconcile": True,
            }
        )
        cls.income_account = cls.env["account.account"].create(
            {
                "name": "Membership test income",
                "code": "MTI",
                "account_type": "income",
            }
        )
        cls.sale_journal = cls.env["account.journal"].create(
            {
                "name": "Membership test sales",
                "code": "MTS",
                "type": "sale",
                "company_id": cls.env.company.id,
                "default_account_id": cls.income_account.id,
            }
        )
        cls.member_partner = cls.env["res.partner"].create(
            {
                "name": "Membership Test Member",
                "property_account_receivable_id": cls.receivable_account.id,
            }
        )
        cls.billing_partner = cls.env["res.partner"].create(
            {
                "name": "Membership Test Billing",
                "property_account_receivable_id": cls.receivable_account.id,
            }
        )

        cls.paid_product = cls._create_product("Membership Gold", 120.0, "MT-GOLD")
        cls.secondary_paid_product = cls._create_product(
            "Membership Silver", 80.0, "MT-SILV"
        )
        cls.free_product = cls._create_product("Membership Free", 0.0, "MT-FREE")
        cls.import_product = cls._create_product("Membership Import", 99.5, "MT-CSV")

    @classmethod
    def _create_product(
        cls,
        name,
        price,
        default_code,
        *,
        company=None,
        category=None,
        income_account=None,
    ):
        template = cls.env["product.template"].create(
            {
                "name": name,
                "type": "service",
                "default_code": default_code,
                "list_price": price,
                "categ_id": (category or cls.membership_category).id,
                "company_id": company.id if company else False,
                "property_account_income_id": (income_account or cls.income_account).id,
            }
        )
        return template.product_variant_id

    def _create_membership(
        self,
        product,
        *,
        partner=None,
        invoice_partner=None,
        company=None,
        state="draft",
        date_start=None,
        date_end=False,
        amount_override=False,
        is_free_override=False,
        membership_number=False,
    ):
        partner = partner or self.member_partner
        company = company or self.env.company
        vals = {
            "partner_id": partner.id,
            "company_id": company.id,
            "product_id": product.id,
            "date_start": date_start or self.today,
            "state": state,
        }
        if invoice_partner:
            vals["invoice_partner_id"] = invoice_partner.id
        if date_end:
            vals["date_end"] = date_end
        if amount_override is not False:
            vals["amount_override"] = amount_override
        if is_free_override:
            vals["is_free_override"] = True
        if membership_number is not False:
            vals["membership_number"] = membership_number
        return self.env["membership.membership"].create(vals)

    def _create_invoice_for_contribution(self, contribution):
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": contribution.invoice_partner_id.id,
                "company_id": self.env.company.id,
                "journal_id": self.sale_journal.id,
                "invoice_date": self.today,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": contribution.product_id.display_name,
                            "product_id": contribution.product_id.id,
                            "quantity": 1.0,
                            "price_unit": contribution.amount_expected,
                            "membership_contribution_id": contribution.id,
                        }
                    )
                ],
            }
        )
        self.env.invalidate_all()
        return invoice

    def _make_csv_payload(self, rows):
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        for row_index, row in enumerate(rows):
            if row_index == 0:
                writer.writerow(row.keys())
            writer.writerow(row.values())
        return base64.b64encode(buffer.getvalue().encode("utf-8"))

    def test_membership_creation_defaults_overlap_and_state_transitions(self):
        membership = self._create_membership(self.paid_product)
        preview_membership = self.env["membership.membership"].new(
            {
                "partner_id": self.member_partner.id,
                "company_id": self.env.company.id,
                "product_id": self.paid_product.id,
            }
        )

        self.assertEqual(membership.invoice_partner_id, self.member_partner)
        self.assertEqual(membership.date_start, self.today)
        self.assertEqual(membership.state, "draft")
        self.assertIn(self.member_partner.display_name, membership.name)
        self.assertTrue(preview_membership.membership_number_preview)
        self.assertRegex(
            preview_membership.membership_number_preview,
            rf"MEM/{self.today.year}/\d{{5}}",
        )

        with self.assertRaises(UserError):
            membership.write({"state": "active"})

        membership.action_submit()
        self.assertEqual(membership.state, "waiting")

        membership.action_activate()
        self.assertEqual(membership.state, "active")

        membership._do_transition(
            "cancelled",
            date_cancelled=self.today,
            cancel_reason="No longer needed.",
        )
        self.assertEqual(membership.state, "cancelled")
        self.assertEqual(membership.date_cancelled, self.today)
        self.assertEqual(membership.date_end, self.today.replace(month=12, day=31))

        membership.action_reopen_waiting()
        self.assertEqual(membership.state, "waiting")

        membership.action_revert_to_draft()
        self.assertEqual(membership.state, "draft")

        with self.assertRaises(ValidationError):
            self._create_membership(
                self.paid_product,
                partner=self.member_partner,
                date_start=self.today,
            )

    def test_membership_state_group_expand_keeps_all_status_columns(self):
        state_field = self.env["membership.membership"]._fields["state"]

        self.assertEqual(state_field.group_expand, "_read_group_state")
        self.assertEqual(
            self.env["membership.membership"]._read_group_state([], []),
            ["draft", "waiting", "active", "cancelled"],
        )

    def test_member_number_auto_generation_uses_company_format_and_is_global(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Membership Number Company",
                "membership_product_category_id": self.membership_category.id,
                "member_number_prefix": "ORG/%(year)s/",
                "member_number_padding": 3,
            }
        )
        other_product = self._create_product(
            "Membership Number Silver",
            50.0,
            "MT-NUMBER",
            company=other_company,
            category=self.membership_category,
        )

        first_membership = self._create_membership(self.paid_product)
        second_membership = self._create_membership(other_product, company=other_company)

        self.assertTrue(re.fullmatch(rf"MEM/{self.today.year}/\d{{5}}", first_membership.membership_number))
        self.assertTrue(re.fullmatch(rf"ORG/{self.today.year}/\d{{3}}", second_membership.membership_number))
        self.assertNotEqual(first_membership.membership_number, second_membership.membership_number)

    def test_member_number_manual_override_is_preserved_and_unique(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Manual Number Company",
                "membership_product_category_id": self.membership_category.id,
            }
        )
        other_product = self._create_product(
            "Membership Manual Number",
            70.0,
            "MT-MANUAL",
            company=other_company,
            category=self.membership_category,
        )

        membership = self._create_membership(self.paid_product, membership_number="MANUAL-0001")
        self.assertEqual(membership.membership_number, "MANUAL-0001")

        with self.assertRaises(ValidationError):
            self._create_membership(
                other_product,
                company=other_company,
                membership_number="MANUAL-0001",
            )

    def test_legacy_external_ref_values_are_backfilled_into_membership_number(self):
        membership = self._create_membership(self.paid_product)

        self.env.cr.execute(
            "ALTER TABLE membership_membership ADD COLUMN IF NOT EXISTS external_ref VARCHAR"
        )
        self.env.cr.execute(
            "UPDATE membership_membership SET membership_number = NULL, external_ref = %s WHERE id = %s",
            ("LEGACY-0001", membership.id),
        )

        self.env["membership.membership"]._migrate_legacy_membership_numbers()
        self.env.invalidate_all()

        self.assertEqual(
            self.env["membership.membership"].browse(membership.id).membership_number,
            "LEGACY-0001",
        )

    def test_membership_number_import_create_update_and_preserve(self):
        initial_payload = self._make_csv_payload(
            [
                {
                    "partner_external_ref": "PARTNER-NUMBER-001",
                    "partner_name": "Membership Number Partner",
                    "product_code": self.import_product.default_code,
                    "date_start": self.today.isoformat(),
                    "state": "waiting",
                    "membership_number": "IMPORT-0001",
                }
            ]
        )
        wizard = self.env["membership.import.wizard"].create(
            {
                "file": initial_payload,
                "filename": "memberships.csv",
                "company_id": self.env.company.id,
            }
        )
        wizard.action_run()
        membership = self.env["membership.membership"].search(
            [("membership_number", "=", "IMPORT-0001")],
            limit=1,
        )
        self.assertEqual(membership.membership_number, "IMPORT-0001")

        update_payload = self._make_csv_payload(
            [
                {
                    "partner_external_ref": "PARTNER-NUMBER-001",
                    "partner_name": "Membership Number Partner",
                    "product_code": self.import_product.default_code,
                    "date_start": self.today.isoformat(),
                    "state": "waiting",
                    "membership_number": "IMPORT-0002",
                }
            ]
        )
        wizard.write({"file": update_payload, "filename": "memberships.csv"})
        wizard.action_run()
        self.env.invalidate_all()
        partner = self.env["res.partner"].search([("ref", "=", "PARTNER-NUMBER-001")], limit=1)
        membership = self.env["membership.membership"].search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
                ("product_id", "=", self.import_product.id),
                ("date_start", "=", self.today),
            ],
            limit=1,
        )
        self.assertEqual(membership.membership_number, "IMPORT-0002")

        preserve_payload = self._make_csv_payload(
            [
                {
                    "partner_external_ref": "PARTNER-NUMBER-001",
                    "partner_name": "Membership Number Partner",
                    "product_code": self.import_product.default_code,
                    "date_start": self.today.isoformat(),
                    "state": "waiting",
                }
            ]
        )
        wizard.write({"file": preserve_payload, "filename": "memberships.csv"})
        wizard.action_run()
        self.env.invalidate_all()
        membership = self.env["membership.membership"].search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
                ("product_id", "=", self.import_product.id),
                ("date_start", "=", self.today),
            ],
            limit=1,
        )
        self.assertEqual(membership.membership_number, "IMPORT-0002")

    def test_membership_navigation_uses_membership_kanban_overview(self):
        overview_action = self.env.ref("association_membership.action_members_overview")
        root_menu = self.env.ref("association_membership.menu_membership_root")

        self.assertEqual(overview_action.name, "Memberships")
        self.assertEqual(overview_action.res_model, "membership.membership")
        self.assertEqual(overview_action.view_mode, "kanban,list,form")
        self.assertEqual(overview_action.domain, "[]")
        self.assertEqual(
            overview_action.view_id,
            self.env.ref("association_membership.view_membership_membership_kanban"),
        )
        self.assertIn("membership_number", overview_action.view_id.arch_db)
        self.assertNotIn("current_member_numbers_kanban", overview_action.view_id.arch_db)
        self.assertEqual(root_menu.action, overview_action)

        records_menu = self.env.ref("association_membership.menu_membership_memberships")
        config_menu = self.env.ref("association_membership.menu_membership_configuration")
        renewal_menu = self.env.ref("association_membership.menu_membership_renewal")

        self.assertEqual(records_menu.name, "Membership Records")
        self.assertEqual(records_menu.parent_id, root_menu)
        self.assertEqual(config_menu.parent_id, root_menu)
        self.assertEqual(renewal_menu.parent_id, config_menu)
        self.assertFalse(
            self.env["ir.ui.menu"].search_count([("name", "=", "Members Overview")])
        )
        self.assertFalse(
            self.env["ir.ui.menu"].search_count([("name", "=", "Import")])
        )

    def test_membership_and_contribution_defaults_follow_invoice_contact(self):
        member_partner = self.env["res.partner"].create(
            {
                "name": "Invoice Contact Member",
                "property_account_receivable_id": self.receivable_account.id,
            }
        )
        invoice_contact = self.env["res.partner"].create(
            {
                "name": "Invoice Contact Child",
                "parent_id": member_partner.id,
                "type": "invoice",
                "property_account_receivable_id": self.receivable_account.id,
            }
        )

        membership = self._create_membership(self.paid_product, partner=member_partner)
        action = member_partner.action_create_membership()
        contribution_action = membership.action_create_contribution()
        defaults = self.env["membership.contribution"].with_context(
            default_membership_id=membership.id
        ).default_get(["membership_year", "invoice_partner_id"])
        contribution = self.env["membership.contribution"].new({"membership_id": membership.id})
        contribution._onchange_membership_id()

        self.assertEqual(membership.invoice_partner_id, invoice_contact)
        self.assertEqual(action["context"]["default_invoice_partner_id"], invoice_contact.id)
        self.assertEqual(
            contribution_action["context"]["default_invoice_partner_id"],
            invoice_contact.id,
        )
        self.assertEqual(defaults["membership_year"], self.today.year)
        self.assertEqual(defaults["invoice_partner_id"], invoice_contact.id)
        self.assertEqual(contribution.invoice_partner_id, invoice_contact)
        self.assertEqual(contribution.membership_year, self.today.year)

    def test_contact_member_number_kanban_display_lists_all_current_memberships(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Kanban Number Company",
                "membership_product_category_id": self.membership_category.id,
                "member_number_prefix": "KAN/%(year)s/",
                "member_number_padding": 4,
            }
        )
        other_product = self._create_product(
            "Kanban Number Membership",
            40.0,
            "MT-KANBAN",
            company=other_company,
            category=self.membership_category,
        )
        self.env.user.write({"company_ids": [Command.link(other_company.id)]})

        waiting_membership = self._create_membership(
            self.paid_product,
            state="waiting",
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
        )
        active_membership = self._create_membership(
            other_product,
            state="active",
            company=other_company,
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
        )
        cancelled_membership = self._create_membership(
            self.secondary_paid_product,
            state="cancelled",
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
        )
        self.env.user.write({"company_ids": [Command.unlink(other_company.id)]})

        self.env.invalidate_all()
        partner = self.member_partner.with_company(self.env.company)
        kanban_text = partner.current_member_numbers_kanban

        self.assertIn(waiting_membership.membership_number, kanban_text)
        self.assertIn(active_membership.membership_number, kanban_text)
        self.assertNotIn(self.env.company.display_name, kanban_text)
        self.assertNotIn(other_company.display_name, kanban_text)
        self.assertNotIn("Membership:", kanban_text)
        self.assertNotIn("All Memberships:", kanban_text)
        self.assertNotIn(cancelled_membership.membership_number, kanban_text)

    def test_contact_member_number_display_keeps_current_company_and_builds_all_memberships(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Display Number Company",
                "membership_product_category_id": self.membership_category.id,
                "member_number_prefix": "DIS/%(year)s/",
                "member_number_padding": 4,
            }
        )
        other_product = self._create_product(
            "Display Number Membership",
            40.0,
            "MT-DISPLAY",
            company=other_company,
            category=self.membership_category,
        )
        self.env.user.write({"company_ids": [Command.link(other_company.id)]})

        waiting_membership = self._create_membership(
            self.paid_product,
            state="waiting",
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
        )
        active_membership = self._create_membership(
            other_product,
            state="active",
            company=other_company,
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
        )
        cancelled_membership = self._create_membership(
            self.secondary_paid_product,
            state="cancelled",
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
        )
        self.env.user.write({"company_ids": [Command.unlink(other_company.id)]})

        self.env.invalidate_all()
        partner = self.member_partner.with_company(self.env.company)
        primary_number = partner.current_membership_number_display
        all_numbers = partner.all_membership_numbers_display

        self.assertEqual(primary_number, waiting_membership.membership_number)
        self.assertIn(waiting_membership.membership_number, all_numbers)
        self.assertIn(active_membership.membership_number, all_numbers)
        self.assertIn(other_company.display_name, all_numbers)
        self.assertIn("/web#id=%s" % other_company.partner_id.id, all_numbers)
        self.assertNotIn(cancelled_membership.membership_number, all_numbers)

    def test_contribution_creation_is_hybrid_and_free_memberships_stay_waived(self):
        membership = self._create_membership(
            self.paid_product,
            state="waiting",
            invoice_partner=self.billing_partner,
        )
        contribution = self.env["membership.contribution"].create(
            {
                "membership_id": membership.id,
                "membership_year": self.today.year,
            }
        )
        self.env.invalidate_all()

        self.assertFalse(contribution.is_free)
        self.assertEqual(contribution.amount_expected, self.paid_product.list_price)
        self.assertFalse(contribution.invoice_id)
        self.assertEqual(contribution.amount_paid, 0.0)
        self.assertEqual(contribution.billing_status, "to_invoice")

        contribution.write({"amount_paid": 25.0, "billing_status": "partial"})
        self.env.invalidate_all()
        self.assertEqual(contribution.amount_paid, 25.0)
        self.assertEqual(contribution.billing_status, "partial")

        invoice = self._create_invoice_for_contribution(contribution)
        invoice.invoice_line_ids.filtered(
            lambda line: line.membership_contribution_id == contribution
        ).write({"price_unit": 150.0})
        self.env.invalidate_all()

        self.assertEqual(contribution.invoice_id, invoice)
        self.assertEqual(contribution.amount_expected, 150.0)
        self.assertEqual(contribution.billing_status, "invoiced")
        self.assertEqual(contribution.membership_year_display, str(self.today.year))

        free_membership = self._create_membership(
            self.free_product,
            state="waiting",
            invoice_partner=self.billing_partner,
        )
        free_contribution = self.env["membership.contribution"].create(
            {
                "membership_id": free_membership.id,
                "membership_year": self.today.year,
            }
        )

        self.assertTrue(free_contribution.is_free)
        self.assertEqual(free_contribution.amount_expected, 0.0)
        self.assertEqual(free_contribution.billing_status, "waived")
        self.assertFalse(free_contribution.invoice_id)

        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.env["membership.contribution"].create(
                    {
                        "membership_id": membership.id,
                        "membership_year": self.today.year,
                    }
                )

    def test_zero_amount_override_keeps_zero_expected_amount_and_zero_value_invoice(self):
        membership = self._create_membership(
            self.paid_product,
            state="waiting",
            invoice_partner=self.billing_partner,
            amount_override=0.0,
        )
        contribution = self.env["membership.contribution"].create(
            {
                "membership_id": membership.id,
                "membership_year": self.today.year,
            }
        )
        self.env.invalidate_all()

        self.assertEqual(membership._resolve_amount_expected(), 0.0)
        self.assertEqual(contribution.amount_expected, 0.0)
        self.assertFalse(contribution.invoice_id)

        invoice = contribution._create_membership_invoices()[:1]
        self.env.invalidate_all()

        self.assertEqual(contribution.invoice_id, invoice)
        self.assertEqual(contribution.invoice_line_id.price_unit, 0.0)

    def test_renewal_groups_paid_memberships_and_creates_free_contributions(self):
        target_year = self.next_year
        paid_a = self._create_membership(
            self.paid_product,
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
            state="active",
            date_start=self.today - timedelta(days=30),
        )
        paid_b = self._create_membership(
            self.secondary_paid_product,
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
            state="active",
            date_start=self.today - timedelta(days=30),
        )
        free_membership = self._create_membership(
            self.free_product,
            partner=self.member_partner,
            invoice_partner=self.billing_partner,
            state="active",
            date_start=self.today - timedelta(days=30),
        )

        wizard = self.env["membership.renewal.wizard"].create(
            {
                "target_year": target_year,
                "company_ids": [(6, 0, [self.env.company.id])],
                "dry_run": False,
                "invoice_date": self.today,
                "auto_post": False,
            }
        )
        wizard.action_run()
        self.env.invalidate_all()

        result_lines = wizard.result_line_ids
        self.assertEqual(len(result_lines), 3)
        self.assertEqual(len(result_lines.filtered(lambda line: line.status == "created")), 3)

        target_contributions = self.env["membership.contribution"].search(
            [
                ("membership_id", "in", [paid_a.id, paid_b.id, free_membership.id]),
                ("membership_year", "=", target_year),
            ]
        )
        self.assertEqual(len(target_contributions), 3)

        free_contribution = target_contributions.filtered(lambda line: line.membership_id == free_membership)
        self.assertTrue(free_contribution.is_free)
        self.assertFalse(free_contribution.invoice_id)

        paid_contributions = target_contributions.filtered(lambda line: line.membership_id != free_membership)
        self.assertEqual(len(paid_contributions.mapped("invoice_id")), 1)
        invoice = paid_contributions[0].invoice_id
        self.assertEqual(invoice.partner_id, self.billing_partner)
        self.assertEqual(
            set(invoice.invoice_line_ids.filtered(lambda line: line.membership_id).mapped("membership_id").ids),
            {paid_a.id, paid_b.id},
        )
        self.assertEqual(len(result_lines.filtered(lambda line: line.invoice_id)), 2)

    def test_renewal_group_rollback_is_atomic(self):
        paid_a = self._create_membership(
            self.paid_product,
            state="active",
            invoice_partner=self.billing_partner,
            date_start=self.today - timedelta(days=30),
        )
        paid_b = self._create_membership(
            self.secondary_paid_product,
            state="active",
            invoice_partner=self.billing_partner,
            date_start=self.today - timedelta(days=30),
        )
        wizard = self.env["membership.renewal.wizard"].create(
            {
                "target_year": self.next_year,
                "company_ids": [(6, 0, [self.env.company.id])],
                "invoice_date": self.today,
            }
        )

        contribution_model = type(self.env["membership.contribution"])
        original = contribution_model._create_membership_invoices

        def broken(records, auto_post=False, invoice_date=False):
            original(records, auto_post=auto_post, invoice_date=invoice_date)
            raise UserError("Simulated renewal invoice failure")

        with patch.object(
            contribution_model,
            "_create_membership_invoices",
            autospec=True,
            side_effect=broken,
        ):
            wizard.action_run()
        self.env.invalidate_all()

        self.assertEqual(
            len(wizard.result_line_ids.filtered(lambda line: line.status == "error")),
            2,
        )
        self.assertFalse(
            self.env["membership.contribution"].search_count(
                [
                    ("membership_id", "in", [paid_a.id, paid_b.id]),
                    ("membership_year", "=", self.next_year),
                ]
            )
        )
        self.assertFalse(
            self.env["account.move"].search(
                [("move_type", "=", "out_invoice"), ("partner_id", "=", self.billing_partner.id)]
            ).filtered(
                lambda move: set(move.invoice_line_ids.mapped("membership_id").ids) == {paid_a.id, paid_b.id}
            )
        )

    def test_import_is_idempotent_on_repeated_csv_rows(self):
        csv_payload = self._make_csv_payload(
            [
                {
                    "partner_external_ref": "PARTNER-CSV-001",
                    "partner_name": "CSV Membership Partner",
                    "product_code": self.import_product.default_code,
                    "date_start": self.today.isoformat(),
                    "state": "waiting",
                    "membership_year": str(self.next_year),
                    "amount_expected": "99.5",
                    "is_free": "false",
                }
            ]
        )

        wizard = self.env["membership.import.wizard"].create(
            {
                "file": csv_payload,
                "filename": "memberships.csv",
                "company_id": self.env.company.id,
            }
        )
        wizard.action_run()
        wizard.action_run()
        self.env.invalidate_all()

        partner = self.env["res.partner"].search(
            [("ref", "=", "PARTNER-CSV-001")], limit=1
        )
        membership = self.env["membership.membership"].search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
                ("product_id", "=", self.import_product.id),
                ("date_start", "=", self.today),
            ],
            limit=1,
        )
        contribution = self.env["membership.contribution"].search(
            [
                ("membership_id", "=", membership.id),
                ("membership_year", "=", self.next_year),
            ],
            limit=1,
        )

        self.assertTrue(partner)
        self.assertEqual(len(self.env["res.partner"].search([("ref", "=", "PARTNER-CSV-001")])), 1)
        self.assertEqual(len(membership), 1)
        self.assertEqual(len(contribution), 1)
        self.assertEqual(membership.state, "waiting")
        self.assertEqual(contribution.amount_expected, 99.5)
        self.assertFalse(contribution.invoice_id)

    def test_import_uses_company_compatible_product(self):
        other_company = self.env["res.company"].create(
            {
                "name": "Second Membership Company",
                "membership_product_category_id": self.membership_category.id,
            }
        )
        self._create_product(
            "AAA Wrong Company Product",
            50.0,
            "MT-DUPL",
            company=other_company,
        )
        correct_product = self._create_product(
            "ZZZ Correct Company Product",
            60.0,
            "MT-DUPL",
            company=self.env.company,
        )

        wizard = self.env["membership.import.wizard"].create(
            {
                "file": self._make_csv_payload(
                    [
                        {
                            "partner_external_ref": "PARTNER-CSV-COMPANY",
                            "partner_name": "Company-specific product partner",
                            "product_code": "MT-DUPL",
                            "date_start": self.today.isoformat(),
                            "state": "waiting",
                        }
                    ]
                ),
                "filename": "memberships.csv",
                "company_id": self.env.company.id,
            }
        )

        wizard.action_run()
        self.env.invalidate_all()

        partner = self.env["res.partner"].search([("ref", "=", "PARTNER-CSV-COMPANY")], limit=1)
        membership = self.env["membership.membership"].search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.env.company.id),
                ("product_id", "=", correct_product.id),
                ("date_start", "=", self.today),
            ],
            limit=1,
        )
        self.assertEqual(membership.product_id, correct_product)

    def test_company_specific_settings_are_isolated(self):
        other_category = self.env["product.category"].create({"name": "Other Membership Category"})
        other_company = self.env["res.company"].create(
            {
                "name": "Configurable Membership Company",
                "membership_product_category_id": other_category.id,
                "membership_auto_activate_on_payment": True,
                "membership_cron_year_offset": 3,
                "membership_cron_auto_post": True,
                "member_number_prefix": "ALT/%(year)s/",
                "member_number_padding": 7,
            }
        )
        other_product = self._create_product(
            "Other Company Membership",
            45.0,
            "MT-OTHER",
            category=other_category,
        )

        self.assertEqual(
            self.env["membership.membership"]._get_membership_category(company=self.env.company),
            self.membership_category,
        )
        self.assertEqual(
            self.env["membership.membership"]._get_membership_category(company=other_company),
            other_category,
        )
        self.assertFalse(
            self.env["membership.membership"]._is_auto_activate_on_payment_enabled(company=self.env.company)
        )
        self.assertTrue(
            self.env["membership.membership"]._is_auto_activate_on_payment_enabled(company=other_company)
        )
        self.assertEqual(
            self.env["membership.membership"]._cron_target_year(company=other_company),
            self.today.year + 3,
        )
        self.assertEqual(other_company._render_member_number_prefix(), f"ALT/{self.today.year}/")
        self.assertEqual(other_company.member_number_padding, 7)

        self._create_membership(
            other_product,
            company=other_company,
            invoice_partner=self.billing_partner,
        )
        with self.assertRaises(ValidationError):
            self._create_membership(
                self.paid_product,
                company=other_company,
                invoice_partner=self.billing_partner,
            )

    def test_membership_product_domain_follows_selected_company(self):
        other_category = self.env["product.category"].create({"name": "Other Membership Category 2"})
        other_company = self.env["res.company"].create(
            {
                "name": "Domain Membership Company",
                "membership_product_category_id": other_category.id,
            }
        )
        other_product = self._create_product(
            "Other Domain Membership",
            45.0,
            "MT-DOMAIN",
            company=other_company,
            category=other_category,
        )

        membership = self.env["membership.membership"].new({"company_id": other_company.id})
        onchange_result = membership._onchange_company_id()

        self.assertEqual(membership.membership_category_id, other_category)
        self.assertIn(("categ_id", "child_of", other_category.id), onchange_result["domain"]["product_id"])
        self.assertIn(("company_id", "=", other_company.id), onchange_result["domain"]["product_id"])
        self.assertTrue(
            self.env["product.product"].search_count(
                onchange_result["domain"]["product_id"] + [("id", "=", other_product.id)]
            )
        )


    def test_member_number_settings_validate_padding(self):
        with self.assertRaises(ValidationError):
            with self.env.cr.savepoint():
                self.env.company.write({"member_number_padding": 0})

    def test_paid_contribution_invoice_creation_requires_sale_journal(self):
        other_company = self.env["res.company"].create(
            {
                "name": "No Sales Journal Company",
                "membership_product_category_id": self.membership_category.id,
            }
        )
        self.env["account.journal"].search(
            [("company_id", "=", other_company.id), ("type", "=", "sale")]
        ).unlink()
        membership = self._create_membership(
            self.paid_product,
            company=other_company,
            state="waiting",
            invoice_partner=self.billing_partner,
        )
        contribution = self.env["membership.contribution"].create(
            {
                "membership_id": membership.id,
                "membership_year": self.today.year,
            }
        )

        self.assertFalse(contribution.invoice_id)
        with self.assertRaises(UserError):
            contribution._create_membership_invoices()
        self.assertFalse(contribution.invoice_id)

    def test_multi_create_contribution_backfills_invoice_from_each_row(self):
        first_membership = self._create_membership(
            self.paid_product,
            state="waiting",
            invoice_partner=self.billing_partner,
        )
        second_membership = self._create_membership(
            self.secondary_paid_product,
            state="waiting",
            invoice_partner=self.billing_partner,
        )
        invoice = self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "partner_id": self.billing_partner.id,
                "company_id": self.env.company.id,
                "journal_id": self.sale_journal.id,
                "invoice_date": self.today,
                "invoice_line_ids": [
                    Command.create(
                        {
                            "name": first_membership.product_id.display_name,
                            "product_id": first_membership.product_id.id,
                            "quantity": 1.0,
                            "price_unit": first_membership.product_id.list_price,
                            "membership_id": first_membership.id,
                            "membership_year": self.today.year,
                        }
                    )
                ],
            }
        )
        invoice_line = invoice.invoice_line_ids.filtered("membership_id")[:1]

        contributions = self.env["membership.contribution"].with_context(
            skip_membership_invoice_creation=True
        ).create(
            [
                {
                    "membership_id": first_membership.id,
                    "membership_year": self.today.year,
                    "invoice_line_id": invoice_line.id,
                },
                {
                    "membership_id": second_membership.id,
                    "membership_year": self.today.year,
                },
            ]
        )

        self.assertEqual(contributions[0].invoice_id, invoice)
        self.assertEqual(contributions[0].invoice_line_id, invoice_line)
        self.assertFalse(contributions[1].invoice_id)

    def test_auto_activation_on_paid_invoice(self):
        self.env.company.membership_auto_activate_on_payment = True
        membership = self._create_membership(
            self.paid_product,
            state="waiting",
            invoice_partner=self.billing_partner,
        )
        contribution = self.env["membership.contribution"].create(
            {
                "membership_id": membership.id,
                "membership_year": self.today.year,
            }
        )
        invoice = contribution._create_membership_invoices()[:1]
        invoice.action_post()

        if invoice._fields["payment_state"].readonly:
            self.skipTest("account.move.payment_state is readonly in this environment.")

        invoice.write({"payment_state": "paid"})
        self.env.invalidate_all()

        self.assertEqual(membership.state, "active")
