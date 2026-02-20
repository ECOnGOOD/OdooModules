from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    membership_contract_dec31_default = fields.Boolean(
        related="company_id.membership_contract_dec31_default",
        readonly=False,
    )
