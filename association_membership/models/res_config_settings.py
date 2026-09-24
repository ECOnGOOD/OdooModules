from odoo import _, api, fields, models


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
    member_number_own_sequence = fields.Boolean(
        related="company_id.member_number_own_sequence",
        readonly=False,
    )
    # The format of the sequence the company numbers from. Editable only with own
    # numbering; the default numbering is changed on its sequence by an administrator.
    member_number_prefix = fields.Char(
        string="Prefix",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_suffix = fields.Char(
        string="Suffix",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_padding = fields.Integer(
        string="Digits",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_next = fields.Integer(
        string="Next Number",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_preview = fields.Char(
        string="Next Member Number",
        compute="_compute_member_number_preview",
    )

    @api.depends("company_id", "member_number_own_sequence")
    def _compute_member_number_format(self):
        for record in self:
            sequence = record.company_id._get_membership_number_sequence()
            record.member_number_prefix = sequence.prefix
            record.member_number_suffix = sequence.suffix
            record.member_number_padding = sequence.padding
            record.member_number_next = sequence.number_next_actual

    def _inverse_member_number_format(self):
        for record in self:
            if not record.member_number_own_sequence:
                continue
            # Also covers own numbering switched on in the same save.
            record.company_id._ensure_own_membership_number_sequence().write(
                {
                    "prefix": record.member_number_prefix,
                    "suffix": record.member_number_suffix,
                    "padding": record.member_number_padding,
                    "number_next": record.member_number_next,
                }
            )

    @api.depends(
        "member_number_prefix",
        "member_number_suffix",
        "member_number_padding",
        "member_number_next",
    )
    def _compute_member_number_preview(self):
        sequence_model = self.env["ir.sequence"]
        for record in self:
            # An unsaved sequence formats the values as they stand in the form.
            draft = sequence_model.new(
                {
                    "prefix": record.member_number_prefix,
                    "suffix": record.member_number_suffix,
                    "padding": record.member_number_padding,
                }
            )
            try:
                record.member_number_preview = draft.get_next_char(record.member_number_next)
            except Exception:
                record.member_number_preview = _("Invalid format")

    # Workaround for Odoo 18 core bug in account_peppol
    # The Peppol module exposes this field in the view but restricts it in python,
    # causing a crash for non-admin users. This dummy definition bypasses the crash.
    account_peppol_migration_key = fields.Char(
        string="Migration Key (Bypass)",
        readonly=False,
    )
