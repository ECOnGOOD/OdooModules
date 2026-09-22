from odoo import api, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    membership_company_mail_recipients = fields.Selection(
        related="company_id.membership_company_mail_recipients",
        readonly=False,
    )
    membership_default_period_year = fields.Integer(
        related="company_id.membership_default_period_year",
        readonly=False,
    )
    membership_invoicing_strategy = fields.Selection(
        related="company_id.membership_invoicing_strategy",
        readonly=False,
    )
    membership_cron_year_offset = fields.Integer(
        related="company_id.membership_cron_year_offset",
        readonly=False,
    )
    membership_activation_invoice_template_id = fields.Many2one(
        related="company_id.membership_activation_invoice_template_id",
        readonly=False,
    )
    membership_welcome_template_id = fields.Many2one(
        related="company_id.membership_welcome_template_id",
        readonly=False,
    )
    membership_cancellation_template_id = fields.Many2one(
        related="company_id.membership_cancellation_template_id",
        readonly=False,
    )
    member_number_prefix = fields.Char(
        related="company_id.member_number_prefix",
        readonly=False,
    )
    member_number_padding = fields.Integer(
        related="company_id.member_number_padding",
        readonly=False,
    )
    member_number_own_sequence = fields.Boolean(
        related="company_id.member_number_own_sequence",
        readonly=False,
    )
    member_number_next = fields.Integer(
        string="Next Member Number",
        compute="_compute_member_number_next",
        inverse="_inverse_member_number_next",
    )

    @api.depends("company_id", "member_number_own_sequence")
    def _compute_member_number_next(self):
        for record in self:
            sequence = record.company_id._get_membership_number_sequence()
            record.member_number_next = sequence.number_next_actual if sequence else 1

    def _inverse_member_number_next(self):
        for record in self:
            if record.member_number_next >= 1:
                sequence = record.company_id._get_membership_number_sequence()
                sequence.sudo().write({"number_next": record.member_number_next})

    member_number_preview = fields.Char(
        string="Next Member Number (Preview)",
        compute="_compute_member_number_preview",
    )

    @api.depends(
        "company_id",
        "member_number_prefix",
        "member_number_padding",
        "member_number_next",
        "member_number_own_sequence",
    )
    def _compute_member_number_preview(self):
        for record in self:
            sequence = record.company_id._get_membership_number_sequence()
            counter = record.member_number_next or (
                sequence.number_next_actual if sequence else 1
            )
            try:
                prefix = record.company_id._render_member_number_prefix()
            except Exception:
                prefix = record.company_id.member_number_prefix or ""
            record.member_number_preview = "%s%s" % (
                prefix,
                str(counter).zfill(record.company_id.member_number_padding),
            )

    # Workaround for Odoo 18 core bug in account_peppol
    # The Peppol module exposes this field in the view but restricts it in python,
    # causing a crash for non-admin users. This dummy definition bypasses the crash.
    account_peppol_migration_key = fields.Char(
        string="Migration Key (Bypass)",
        readonly=False,
    )
