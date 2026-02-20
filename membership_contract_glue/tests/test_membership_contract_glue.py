from odoo.tests.common import TransactionCase


class TestMembershipContractGlue(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Member A"})

    def test_action_create_membership_contract_defaults_enabled(self):
        self.env.company.membership_contract_yearly_defaults = True
        action = self.env.ref(
            "membership_contract_glue.action_create_membership_contract_from_partner"
        ).read()[0]
        ctx = action["context"]

        self.assertEqual(action["res_model"], "contract.contract")
        self.assertEqual(action["target"], "new")
        self.assertTrue(ctx["default_is_membership_contract"])
        self.assertEqual(ctx["default_contract_type"], "sale")
        self.assertTrue(ctx["create_membership_contract_from_partner"])

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

        self.assertNotIn("recurring_interval", defaults)
        self.assertNotIn("recurring_rule_type", defaults)
        self.assertNotIn("recurring_next_date", defaults)
