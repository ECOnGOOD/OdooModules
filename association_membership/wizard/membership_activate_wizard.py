from odoo import Command, _, api, fields, models

from ..models.res_company import INVOICING_STRATEGY_SELECTION


class MembershipActivateWizard(models.TransientModel):
    _name = "membership.activate.wizard"
    _description = "Membership Activate Wizard"

    membership_id = fields.Many2one(
        "membership.membership",
        required=True,
        readonly=True,
    )
    invoicing_strategy = fields.Selection(
        selection=INVOICING_STRATEGY_SELECTION,
        string="Invoicing Strategy",
        default=lambda self: self.env.company.membership_invoicing_strategy,
        help="Applies from now on. Changing it here also sets it on the membership.",
    )

    period_year = fields.Integer(readonly=True)
    has_period = fields.Boolean(readonly=True)
    create_period = fields.Boolean(string="Create Period")
    invoice_id = fields.Many2one("account.move", readonly=True)
    send_invoice_email = fields.Boolean(string="Send Invoice Email")
    free_period_warning = fields.Char(compute="_compute_free_period_warning")

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
        period_year = membership._default_period_year()
        has_period = period_year in membership.period_ids.mapped("membership_year")
        defaults["period_year"] = period_year
        defaults["has_period"] = has_period
        defaults["create_period"] = not has_period
        strategy = membership._get_invoicing_strategy()
        defaults["invoicing_strategy"] = strategy
        invoice = self._get_current_year_invoice(membership)
        if invoice:
            defaults["invoice_id"] = invoice.id
        # An invoice can only be sent once it is posted, which only `confirm`
        # does. `draft` deliberately leaves it in draft.
        defaults["send_invoice_email"] = False

        defaults["mail_partner_ids"] = [(6, 0, membership._get_communication_partners().ids)]

        template = membership.company_id.membership_welcome_template_id
        # Only a first activation welcomes anybody: reactivating a cancelled
        # membership, or activating one reverted to draft, must not send it
        # again. Such a membership has periods or a welcome date.
        first_activation = (
            membership.state in ("draft", "waiting")
            and not membership.date_welcome_sent
            and not membership.period_ids
        )
        defaults["send_welcome_message"] = bool(template) and first_activation
        if template:
            defaults["welcome_template_id"] = template.id
            defaults["mail_subject"] = membership._render_mail_template_field(template, "subject") or ""
            defaults["mail_body"] = membership._render_mail_template_field(template, "body_html") or ""
        return defaults

    @api.model
    def _get_current_year_invoice(self, membership):
        """The current year's invoice, draft or posted."""
        target_year = membership._default_period_year()
        period = membership.period_ids.filtered(
            lambda c: c.membership_year == target_year
            and c.invoice_id
            and c.invoice_id.state in ("draft", "posted")
        )[:1]
        return period.invoice_id if period else self.env["account.move"]

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

    @api.depends("invoicing_strategy", "create_period", "has_period", "membership_id.amount")
    def _compute_free_period_warning(self):
        """A zero fee cannot be invoiced - say so instead of doing nothing."""
        for wizard in self:
            wizard.free_period_warning = False
            if wizard.invoicing_strategy == "manual" or not wizard.membership_id:
                continue
            if not wizard.create_period and not wizard.has_period:
                continue
            if wizard.membership_id.amount:
                continue
            wizard.free_period_warning = _(
                "The membership fee is 0, so no invoice will be created."
                " Set an amount on the membership first if one is owed."
            )

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
        # The email itself is logged on the invoice; the membership gets one line.
        self.membership_id.message_post(
            body=_("Invoice %(invoice)s sent to %(recipient)s.")
            % {
                "invoice": self.invoice_id.display_name,
                "recipient": self.invoice_id.partner_id.display_name,
            },
            subtype_xmlid="mail.mt_note",
        )
        return True

    def _create_welcome_mail_composer(self):
        self.ensure_one()
        composer = (
            self.env["mail.compose.message"]
            .with_context(
                default_composition_mode="comment",
                # The sender does not become a follower (5.3).
                mail_create_nosubscribe=True,
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
        membership = self.membership_id
        # Set the strategy first: the period billed below freezes it.
        if self.invoicing_strategy != membership._get_invoicing_strategy():
            membership.invoicing_strategy = self.invoicing_strategy
        if membership.state == "draft":
            # "New" leaves the form in draft, so activating from there is one
            # click rather than Submit followed by Activate.
            membership._do_transition("waiting")
        membership._do_transition("active")
        if self.create_period or self.has_period:
            # Invoices an existing, never-billed period too, not only a new one.
            membership._ensure_default_year_period()
        # The period may have just produced the invoice, so resolve it
        # again rather than trusting what default_get saw. The strategy alone
        # decides whether it is a draft or already posted.
        if not self.invoice_id:
            self.invoice_id = self._get_current_year_invoice(membership)
        self._send_invoice_email()
        self._send_welcome_message()
        return {"type": "ir.actions.act_window_close"}
