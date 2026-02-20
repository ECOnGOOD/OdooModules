from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    membership_contract_dec31_default = fields.Boolean(
        string="Default membership contracts to Dec 31",
        default=True,
        help=(
            "When enabled, new lines on membership contracts default their end date "
            "to December 31 of the start year."
        ),
    )
