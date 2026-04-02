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
    member_number_prefix = fields.Char(
        related="company_id.member_number_prefix",
        readonly=False,
    )
    member_number_padding = fields.Integer(
        related="company_id.member_number_padding",
        readonly=False,
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
            record.membership_default_contribution_year = normalize_year_value(
                record.membership_default_contribution_year_text,
                record._fields["membership_default_contribution_year"].string,
            )
