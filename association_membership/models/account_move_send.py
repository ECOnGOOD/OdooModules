from odoo import models


class AccountMoveSend(models.AbstractModel):
    _inherit = "account.move.send"

    def _send_mail(self, move, mail_template, **kwargs):
        result = super()._send_mail(move, mail_template, **kwargs)
        move._mark_membership_welcome_sent(mail_template=mail_template)
        return result
