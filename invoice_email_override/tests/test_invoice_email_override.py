from odoo.exceptions import ValidationError
from odoo.tests.common import TransactionCase


class TestInvoiceEmailOverride(TransactionCase):
    def _new_invoice_move(self, partner):
        return self.env["account.move"].new(
            {
                "move_type": "out_invoice",
                "partner_id": partner.id,
            }
        )

    def test_field_validation_rejects_invalid_invoice_email(self):
        with self.assertRaisesRegex(ValidationError, "valid invoice email"):
            self.env["res.partner"].create(
                {
                    "name": "Invalid Invoice Email Company",
                    "is_company": True,
                    "invoice_email": "invalid-email",
                }
            )

    def test_invoice_email_used_in_mail_params_when_present(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Invoice Email Company",
                "is_company": True,
                "email": "regular@example.org",
                "invoice_email": "billing@example.org",
            }
        )
        move = self._new_invoice_move(partner)
        send_service = self.env["account.move.send"]
        mail_partners = send_service._get_default_mail_partner_ids(move, False, self.env.lang)
        mail_params = send_service._get_mail_params(
            move,
            {
                "mail_partner_ids": mail_partners.ids,
                "author_partner_id": False,
            },
        )
        self.assertEqual(mail_params["email_to"], "billing@example.org")

    def test_fallback_to_regular_email_when_invoice_email_missing(self):
        partner = self.env["res.partner"].create(
            {
                "name": "Regular Email Company",
                "is_company": True,
                "email": "regular@example.org",
            }
        )
        move = self._new_invoice_move(partner)
        send_service = self.env["account.move.send"]
        mail_partners = send_service._get_default_mail_partner_ids(move, False, self.env.lang)
        mail_params = send_service._get_mail_params(
            move,
            {
                "mail_partner_ids": mail_partners.ids,
                "author_partner_id": False,
            },
        )
        self.assertIn("regular@example.org", mail_params.get("email_to", ""))

    def test_recipient_partner_identity_is_not_replaced(self):
        partner = self.env["res.partner"].create(
            {
                "name": "No Regular Email Company",
                "is_company": True,
                "invoice_email": "billing-only@example.org",
            }
        )
        move = self._new_invoice_move(partner)
        send_service = self.env["account.move.send"]
        mail_partners = send_service._get_default_mail_partner_ids(move, False, self.env.lang)
        self.assertIn(partner.id, mail_partners.ids)

        mail_params = send_service._get_mail_params(
            move,
            {
                "mail_partner_ids": mail_partners.ids,
                "author_partner_id": False,
            },
        )
        self.assertIn(partner.id, mail_params["partner_ids"])
        self.assertEqual(mail_params["email_to"], "billing-only@example.org")
