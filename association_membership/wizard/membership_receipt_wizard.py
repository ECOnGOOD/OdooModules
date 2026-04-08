from odoo import _, api, fields, models
from odoo.exceptions import UserError


class MembershipReceiptWizard(models.TransientModel):
    _name = "membership.receipt.wizard"
    _description = "Membership Receipt Wizard"

    contribution_id = fields.Many2one(
        "membership.contribution",
        required=True,
        readonly=True,
    )
    available_template_ids = fields.Many2many(
        "mail.template",
        compute="_compute_available_template_ids",
    )
    template_id = fields.Many2one(
        "mail.template",
        string="Receipt Template",
        domain="[('id', 'in', available_template_ids)]",
        required=True,
    )

    @api.depends("contribution_id")
    def _compute_available_template_ids(self):
        for wizard in self:
            company = wizard.contribution_id.company_id
            wizard.available_template_ids = (
                company.membership_membership_receipt_template_id
                | company.membership_donation_receipt_template_id
            )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        contribution_id = defaults.get("contribution_id") or self.env.context.get("default_contribution_id")
        if not contribution_id:
            return defaults
        contribution = self.env["membership.contribution"].browse(contribution_id)
        template = (
            contribution.company_id.membership_membership_receipt_template_id
            or contribution.company_id.membership_donation_receipt_template_id
        )
        if template:
            defaults["template_id"] = template.id
        return defaults

    def action_open_composer(self):
        self.ensure_one()
        if not self.available_template_ids:
            raise UserError(
                _(
                    "Configure at least one receipt email template in Membership Settings first."
                )
            )
        if not self.template_id:
            raise UserError(_("Please choose a receipt template."))
        if self.template_id not in self.available_template_ids:
            raise UserError(_("Please choose one of the configured receipt templates."))
        composer = self.env["mail.compose.message"].with_context(
            default_composition_mode="comment",
            default_model="membership.contribution",
            default_res_ids=self.contribution_id.ids,
            default_template_id=self.template_id.id,
            default_email_layout_xmlid="mail.mail_notification_light",
        ).create(
            {
                "template_id": self.template_id.id,
            }
        )
        composer.partner_ids = [(6, 0, self.contribution_id._get_receipt_partner_ids().ids)]
        return {
            "type": "ir.actions.act_window",
            "name": _("Send Receipt"),
            "res_model": "mail.compose.message",
            "res_id": composer.id,
            "view_mode": "form",
            "target": "new",
            "view_id": self.env.ref("mail.email_compose_message_wizard_form").id,
        }
