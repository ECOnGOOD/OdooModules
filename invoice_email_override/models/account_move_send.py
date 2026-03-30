from odoo import models


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    def _get_default_mail_partner_ids(self, move, mail_template, mail_lang):
        partners = super()._get_default_mail_partner_ids(move, mail_template, mail_lang)
        commercial_partner = move.partner_id.commercial_partner_id
        if commercial_partner and commercial_partner.invoice_email:
            partners |= commercial_partner
        return partners

    def _get_mail_params(self, move, move_data):
        mail_params = super()._get_mail_params(move, move_data)
        commercial_partner = move.partner_id.commercial_partner_id
        invoice_email = (commercial_partner.invoice_email or "").strip()
        if not invoice_email:
            return mail_params

        mail_params["email_to"] = invoice_email
        partner_ids = list(mail_params.get("partner_ids") or [])
        if commercial_partner.id and commercial_partner.id not in partner_ids:
            partner_ids.append(commercial_partner.id)
        mail_params["partner_ids"] = partner_ids
        return mail_params

