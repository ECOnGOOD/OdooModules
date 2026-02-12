from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # 1. Demographics (Specific to companies and/or Commune/Region partners)
    employee_count = fields.Integer(
        string="Number of Employees",
        help="Employee count (FTE)."
    )
    inhabitant_count = fields.Integer(
        string="Number of Inhabitants",
        help="Population count if this partner represents a region or commune."
    )

    # 2. Nonprofit Status
    is_nonprofit = fields.Boolean(
        string="Nonprofit Organization",
        default=False
    )

    # 3. Legal/Compliance Dates
    code_of_conduct_signed_date = fields.Date(
        string="Code of Conduct Signed On"
    )
    privacy_agreement_signed_date = fields.Date(
        string="Privacy Agreement Signed On"
    )

    # 4. Special Email
    # Note: Use Char, but standard Odoo handles email validation on 'email' field. -> this will not be needed in the future, when contacts are linked to users (TODO)
    email_econgood = fields.Char(
        string="ECOnGOOD Email Address"
    )