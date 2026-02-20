from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    membership_contract_yearly_defaults = fields.Boolean(
        string="Default annual cycle for membership contracts",
        oldname="membership_contract_dec31_default",
        default=True,
        help=(
            "When enabled, new membership contracts created from a partner "
            "default to yearly recurrence and next invoice date on next January 1."
        ),
    )
