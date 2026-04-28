from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    de_finanzamt_name = fields.Char(
        related="company_id.de_finanzamt_name",
        readonly=False,
    )
    de_steuernummer = fields.Char(
        related="company_id.de_steuernummer",
        readonly=False,
    )
    de_freistellungsbescheid_date = fields.Date(
        related="company_id.de_freistellungsbescheid_date",
        readonly=False,
    )
    de_freistellungsbescheid_year = fields.Char(
        related="company_id.de_freistellungsbescheid_year",
        readonly=False,
    )
    de_charitable_purpose = fields.Char(
        related="company_id.de_charitable_purpose",
        readonly=False,
    )
    de_signatory_name = fields.Char(
        related="company_id.de_signatory_name",
        readonly=False,
    )
    de_signatory_role = fields.Char(
        related="company_id.de_signatory_role",
        readonly=False,
    )
    de_receipt_is_membership = fields.Boolean(
        related="company_id.de_receipt_is_membership",
        readonly=False,
    )
