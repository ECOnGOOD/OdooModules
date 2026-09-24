from datetime import date

from odoo import Command, _, api, fields, models
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
        default=lambda self: date(fields.Date.context_today(self).year, 12, 31),
    )
    # Required here, not on the membership: imported historical cancellations
    # have no reason, and action_cancel_direct must keep accepting none.
    cancel_reason = fields.Text(required=True)
    open_period_ids = fields.Many2many(
        "membership.period",
        string="Unpaid Periods",
        readonly=True,
    )
    open_period_handling = fields.Selection(
        [
            ("keep", "Keep them"),
            ("drop", "Cancel draft invoices and delete the periods"),
        ],
        string="Unpaid periods",
        default="keep",
        required=True,
        help="Periods of the cancellation year and later that are not paid."
             " Posted invoices are never touched: reverse them with a credit note.",
    )
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
    invoice_partner_included = fields.Boolean(
        compute="_compute_invoice_partner_included",
    )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        membership_id = defaults.get("membership_id") or self.env.context.get("default_membership_id")
        if not membership_id:
            return defaults
        membership = self.env["membership.membership"].browse(membership_id)
        if membership.state == "cancelled":
            # Correcting an existing cancellation: start from what is there.
            defaults["date_cancelled"] = membership.date_cancelled
            defaults["date_end"] = membership.date_end
            defaults["cancel_reason"] = membership.cancel_reason
        cancel_date = fields.Date.to_date(defaults.get("date_cancelled")) or fields.Date.context_today(self)
        defaults["open_period_ids"] = [
            (6, 0, membership._open_periods_from(cancel_date.year).ids)
        ]
        defaults["mail_partner_ids"] = [(6, 0, membership._get_communication_partners().ids)]
        template = membership._get_mail_template("cancellation")
        if template:
            defaults["cancellation_template_id"] = template.id
            defaults["mail_subject"] = membership._render_mail_template_field(template, "subject")
            defaults["mail_body"] = membership._render_mail_template_field(template, "body_html")
        return defaults

    @api.depends("mail_partner_ids", "membership_id.invoice_partner_id")
    def _compute_invoice_partner_included(self):
        for wizard in self:
            invoice_partner = (
                wizard.membership_id._get_invoice_partner()
                if wizard.membership_id
                else self.env["res.partner"]
            )
            wizard.invoice_partner_included = (
                bool(invoice_partner) and invoice_partner in wizard.mail_partner_ids
            )

    def action_add_invoice_partner(self):
        self.ensure_one()
        invoice_partner = self.membership_id._get_invoice_partner()
        if invoice_partner and invoice_partner not in self.mail_partner_ids:
            self.mail_partner_ids = [Command.link(invoice_partner.id)]
        return True

    def _create_cancellation_mail_composer(self):
        self.ensure_one()
        composer = self.env["mail.compose.message"].with_context(
            default_composition_mode="comment",
            # The sender does not become a follower (5.3).
            mail_create_nosubscribe=True,
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
        if self.membership_id.state not in ("active", "cancelled"):
            raise UserError(
                _(
                    "Only active or cancelled memberships can be cancelled. A membership"
                    " that was never active goes back to draft instead."
                )
            )
        values = {
            "date_cancelled": self.date_cancelled,
            "date_end": self.date_end,
            "cancel_reason": self.cancel_reason,
        }
        self.membership_id._schedule_termination(**values)
        if self.open_period_handling == "drop":
            self.open_period_ids._drop_unbilled()
        self._send_cancellation_message()
        return {"type": "ir.actions.act_window_close"}
