from odoo import api, fields, models

from .res_company import normalize_year_value


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    membership_auto_activate_on_payment = fields.Boolean(
        related="company_id.membership_auto_activate_on_payment",
        readonly=False,
    )
    membership_product_category_id = fields.Many2one(
        related="company_id.membership_product_category_id",
        readonly=False,
    )
    membership_default_contribution_year = fields.Integer(
        related="company_id.membership_default_contribution_year",
        readonly=False,
    )
    membership_default_contribution_year_text = fields.Char(
        string="Default Contribution Year Input",
        compute="_compute_membership_default_contribution_year_text",
        inverse="_inverse_membership_default_contribution_year_text",
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
    member_number_next = fields.Integer(
        string="Next Member Number",
        compute="_compute_member_number_next",
        inverse="_inverse_member_number_next",
    )

    @api.depends("company_id")
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

    @api.depends("membership_default_contribution_year")
    def _compute_membership_default_contribution_year_text(self):
        for record in self:
            record.membership_default_contribution_year_text = (
                str(record.membership_default_contribution_year)
                if record.membership_default_contribution_year
                else False
            )

    def _inverse_membership_default_contribution_year_text(self):
        for record in self:
            text = (record.membership_default_contribution_year_text or "").strip()
            if not text:
                # Empty means "no override": new contributions default to the
                # current year.
                record.membership_default_contribution_year = 0
                continue
            record.membership_default_contribution_year = normalize_year_value(
                text,
                record._fields["membership_default_contribution_year"].string,
            )

    # Workaround for Odoo 18 core bug in account_peppol
    # The Peppol module exposes this field in the view but restricts it in python,
    # causing a crash for non-admin users. This dummy definition bypasses the crash.
    account_peppol_migration_key = fields.Char(
        string="Migration Key (Bypass)",
        readonly=False,
    )
