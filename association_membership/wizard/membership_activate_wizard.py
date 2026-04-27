from odoo import _, api, fields, models


class MembershipActivateWizard(models.TransientModel):
    _name = "membership.activate.wizard"
    _description = "Membership Activate Wizard"

    membership_id = fields.Many2one(
        "membership.membership",
        required=True,
        readonly=True,
    )

    has_draft_invoice = fields.Boolean(readonly=True)
    invoice_id = fields.Many2one("account.move", readonly=True)
    confirm_invoice = fields.Boolean(string="Confirm Invoice")
    send_invoice_email = fields.Boolean(string="Send Invoice Email")

    welcome_template_id = fields.Many2one(
        "mail.template",
        string="Welcome Template",
        domain="[('model', '=', 'membership.membership')]",
    )
    send_welcome_message = fields.Boolean(string="Send Welcome Message")
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
        invoice = self._get_current_year_draft_invoice(membership)
        strategy = membership.company_id.membership_invoicing_strategy
        if invoice and strategy != "manual":
            defaults["invoice_id"] = invoice.id
            defaults["has_draft_invoice"] = True
            defaults["confirm_invoice"] = True
            defaults["send_invoice_email"] = strategy == "confirm_send"

        invoice_partner = membership._get_invoice_partner()
        defaults["mail_partner_ids"] = [(6, 0, invoice_partner.ids)] if invoice_partner else []

        template = membership.company_id.membership_welcome_template_id
        defaults["send_welcome_message"] = bool(template)
        if template:
            defaults["welcome_template_id"] = template.id
            defaults["mail_subject"] = membership._render_mail_template_field(template, "subject") or ""
            defaults["mail_body"] = membership._render_mail_template_field(template, "body_html") or ""
        return defaults

    @api.model
    def _get_current_year_draft_invoice(self, membership):
        target_year = membership._default_contribution_year()
        contribution = membership.contribution_ids.filtered(
            lambda c: c.membership_year == target_year and c.invoice_id and c.invoice_id.state == "draft"
        )[:1]
        return contribution.invoice_id if contribution else self.env["account.move"]

    @api.onchange("welcome_template_id")
    def _onchange_welcome_template_id(self):
        if not self.welcome_template_id or not self.membership_id:
            return
        self.mail_subject = self.membership_id._render_mail_template_field(
            self.welcome_template_id, "subject"
        ) or ""
        self.mail_body = self.membership_id._render_mail_template_field(
            self.welcome_template_id, "body_html"
        ) or ""

    def _confirm_invoice(self):
        self.ensure_one()
        if not (self.confirm_invoice and self.invoice_id and self.invoice_id.state == "draft"):
            return False
        self.invoice_id.action_post()
        return True

    def _send_invoice_email(self):
        self.ensure_one()
        if not (self.send_invoice_email and self.invoice_id and self.invoice_id.state == "posted"):
            return False
        template = self.membership_id.company_id.membership_activation_invoice_template_id
        if template:
            self.invoice_id.with_context(force_send=True).message_post_with_source(
                template,
                subtype_xmlid="mail.mt_comment",
            )
        else:
            self.invoice_id.with_context(force_send=True).message_post(
                body=_("Invoice sent."),
                subtype_xmlid="mail.mt_comment",
            )
        return True

    def _create_welcome_mail_composer(self):
        self.ensure_one()
        composer = (
            self.env["mail.compose.message"]
            .with_context(
                default_composition_mode="comment",
                default_model="membership.membership",
                default_res_ids=self.membership_id.ids,
                default_email_layout_xmlid="mail.mail_notification_light",
            )
            .create(
                {
                    "subject": self.mail_subject,
                    "body": self.mail_body,
                }
            )
        )
        composer.partner_ids = [(6, 0, self.mail_partner_ids.ids)]
        return composer

    def _send_welcome_message(self):
        self.ensure_one()
        if not self.send_welcome_message:
            return False
        composer = self._create_welcome_mail_composer()
        composer._action_send_mail()
        self.membership_id.date_welcome_sent = fields.Date.context_today(self)
        return True

    def action_confirm(self):
        self.ensure_one()
        self.membership_id._do_transition("active")
        self._confirm_invoice()
        self._send_invoice_email()
        self._send_welcome_message()
        return {"type": "ir.actions.act_window_close"}
