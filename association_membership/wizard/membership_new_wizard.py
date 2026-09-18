from odoo import Command, api, fields, models


class MembershipNewWizard(models.TransientModel):
    """Create a membership in one step, optionally activating it right away.

    Previews come from an unsaved membership (``_preview_membership``), so the
    wizard uses exactly the rules of the membership itself. Activation, the
    contribution and the welcome email are delegated to the activation wizard.
    """

    _name = "membership.new.wizard"
    _description = "New Membership"

    partner_id = fields.Many2one("res.partner", string="Member", required=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    product_id = fields.Many2one("product.product", string="Membership Product", required=True)
    product_domain = fields.Binary(compute="_compute_preview")
    date_start = fields.Date(required=True, default=fields.Date.context_today)
    invoice_partner_id = fields.Many2one(
        "res.partner",
        string="Invoice Contact",
        compute="_compute_invoice_partner_id",
        store=True,
        readonly=False,
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    amount = fields.Monetary(string="Annual Fee", compute="_compute_preview")
    membership_number_preview = fields.Char(string="Membership Number", compute="_compute_preview")
    activate = fields.Boolean(string="Activate Immediately", default=True)
    contribution_year = fields.Integer(compute="_compute_contribution_year")
    create_contribution = fields.Boolean(string="Create Contribution", default=True)
    send_welcome_message = fields.Boolean(
        compute="_compute_send_welcome_message",
        store=True,
        readonly=False,
    )
    mail_partner_ids = fields.Many2many(
        "res.partner",
        string="Recipients",
        compute="_compute_mail_partner_ids",
        store=True,
        readonly=False,
    )

    def _preview_membership(self):
        self.ensure_one()
        return self.env["membership.membership"].new(
            {
                "partner_id": self.partner_id.id,
                "company_id": self.company_id.id,
                "product_id": self.product_id.id,
                "invoice_partner_id": self.invoice_partner_id.id,
            }
        )

    @api.depends("partner_id", "company_id", "product_id")
    def _compute_preview(self):
        for wizard in self:
            preview = wizard._preview_membership()
            wizard.product_domain = preview.product_domain
            wizard.amount = preview.amount
            wizard.membership_number_preview = preview.membership_number_preview

    @api.depends("partner_id")
    def _compute_invoice_partner_id(self):
        for wizard in self:
            wizard.invoice_partner_id = self.env["membership.membership"]._resolve_default_invoice_partner(
                wizard.partner_id
            )

    @api.depends("company_id")
    def _compute_contribution_year(self):
        for wizard in self:
            wizard.contribution_year = wizard.company_id._membership_contribution_year()

    @api.depends("company_id")
    def _compute_send_welcome_message(self):
        for wizard in self:
            wizard.send_welcome_message = bool(wizard.company_id.membership_welcome_template_id)

    @api.depends("partner_id", "company_id", "invoice_partner_id")
    def _compute_mail_partner_ids(self):
        for wizard in self:
            wizard.mail_partner_ids = (
                wizard._preview_membership()._get_communication_partners()
                if wizard.partner_id
                else self.env["res.partner"]
            )

    @api.onchange("partner_id", "company_id")
    def _onchange_membership_product(self):
        if self.product_id and not self.product_id.filtered_domain(self.product_domain):
            self.product_id = False

    def action_confirm(self):
        self.ensure_one()
        membership = self.env["membership.membership"].create(
            {
                "partner_id": self.partner_id.id,
                "company_id": self.company_id.id,
                "product_id": self.product_id.id,
                "date_start": self.date_start,
                "invoice_partner_id": self.invoice_partner_id.id,
            }
        )
        membership.action_submit()
        if self.activate:
            self.env["membership.activate.wizard"].with_context(
                default_membership_id=membership.id
            ).create(
                {
                    "create_contribution": self.create_contribution,
                    "send_welcome_message": self.send_welcome_message,
                    "mail_partner_ids": [Command.set(self.mail_partner_ids.ids)],
                }
            ).action_confirm()
        return {
            "type": "ir.actions.act_window",
            "res_model": "membership.membership",
            "res_id": membership.id,
            "view_mode": "form",
            "target": "current",
        }
