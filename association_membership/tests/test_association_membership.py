
import base64
import csv
import io
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
        external_ref=False,
        amount_override=False,
        is_free_override=False,
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
        if external_ref:
            vals["external_ref"] = external_ref
        if amount_override is not False:
            vals["amount_override"] = amount_override
        if is_free_override:
            vals["is_free_override"] = True
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

        self.assertEqual(membership.invoice_partner_id, self.member_partner)
        self.assertEqual(membership.state, "draft")
        self.assertIn(self.member_partner.display_name, membership.name)

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

    def test_contribution_creation_auto_invoices_and_statuses(self):
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
        self.assertTrue(contribution.invoice_id)
        self.assertEqual(contribution.invoice_id.partner_id, self.billing_partner)
        self.assertEqual(contribution.billing_status, "invoiced")

        free_membership = self._create_membership(
            self.free_product,
            state="waiting",
            invoice_partner=self.billing_partner,
            external_ref="FREE-MEMBERSHIP",
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

    def test_zero_amount_override_creates_zero_value_invoice(self):
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
        self.assertTrue(contribution.invoice_id)
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
                    "external_ref": "MEM-CSV-001",
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
            [("external_ref", "=", "MEM-CSV-001"), ("company_id", "=", self.env.company.id)],
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
        self.assertTrue(contribution.invoice_id)

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
                            "external_ref": "MEM-CSV-COMPANY",
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

        membership = self.env["membership.membership"].search(
            [("external_ref", "=", "MEM-CSV-COMPANY"), ("company_id", "=", self.env.company.id)],
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


    def test_paid_contribution_create_rolls_back_without_sale_journal(self):
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

        with self.assertRaises(UserError):
            self.env["membership.contribution"].create(
                {
                    "membership_id": membership.id,
                    "membership_year": self.today.year,
                }
            )
        self.assertFalse(
            self.env["membership.contribution"].search_count(
                [
                    ("membership_id", "=", membership.id),
                    ("membership_year", "=", self.today.year),
                ]
            )
        )

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
        invoice = contribution.invoice_id
        invoice.action_post()

        if invoice._fields["payment_state"].readonly:
            self.skipTest("account.move.payment_state is readonly in this environment.")

        invoice.write({"payment_state": "paid"})
        self.env.invalidate_all()

        self.assertEqual(membership.state, "active")
