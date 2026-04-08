from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MembershipCancelWizard(models.TransientModel):
    _name = "membership.cancel.wizard"
    _description = "Membership Cancel Wizard"

    membership_id = fields.Many2one(
        "membership.membership",
        required=True,
        readonly=True,
    )
    date_cancelled = fields.Date(
        required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    date_end = fields.Date(
        required=True,
        default=lambda self: date(fields.Date.today().year, 12, 31),
    )
    cancel_reason = fields.Text()
    cancellation_template_id = fields.Many2one(
        "mail.template",
        readonly=True,
    )
    send_cancellation_message = fields.Boolean()
    mail_partner_ids = fields.Many2many(
        "res.partner",
        string="Recipients",
    )
    mail_subject = fields.Char(string="Subject")
    mail_body = fields.Html(string="Contents", sanitize_style=True)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        membership_id = defaults.get("membership_id") or self.env.context.get("default_membership_id")
        if not membership_id:
            return defaults
        membership = self.env["membership.membership"].browse(membership_id)
        template = membership.company_id.membership_cancellation_template_id
        if template:
            defaults["cancellation_template_id"] = template.id
            defaults["mail_partner_ids"] = [(6, 0, membership.partner_id.ids)]
            defaults["mail_subject"] = membership._render_mail_template_field(template, "subject")
            defaults["mail_body"] = membership._render_mail_template_field(template, "body_html")
        return defaults

    def _create_cancellation_mail_composer(self):
        self.ensure_one()
        composer = self.env["mail.compose.message"].with_context(
            default_composition_mode="comment",
            default_model="membership.membership",
            default_res_ids=self.membership_id.ids,
            default_template_id=self.cancellation_template_id.id,
            default_email_layout_xmlid="mail.mail_notification_light",
        ).create(
            {
                "subject": self.mail_subject,
                "body": self.mail_body,
                "template_id": self.cancellation_template_id.id,
            }
        )
        composer.partner_ids = [(6, 0, self.mail_partner_ids.ids)]
        return composer

    def _send_cancellation_message(self):
        self.ensure_one()
        if not (self.send_cancellation_message and self.cancellation_template_id):
            return False
        composer = self._create_cancellation_mail_composer()
        composer._action_send_mail()
        return True

    def action_confirm(self):
        self.ensure_one()
        if self.membership_id.state != "active":
            raise UserError(_("Only active memberships can be cancelled."))
        values = {
            "date_cancelled": self.date_cancelled,
            "date_end": self.date_end,
            "cancel_reason": self.cancel_reason,
        }
        self.membership_id._schedule_termination(**values)
        self._send_cancellation_message()
        return {"type": "ir.actions.act_window_close"}
