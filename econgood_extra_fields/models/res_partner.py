import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

class ResPartner(models.Model):
    _inherit = "res.partner"

    # Demographics (specific to companies and/or Commune/Region partners)
    employee_count = fields.Integer(
        string="Number of Employees",
        help="Employee count (FTE).",
    )
    inhabitant_count = fields.Integer(
        string="Number of Inhabitants",
        help="Population count if this partner represents a region or commune.",
    )

    # Nonprofit Status
    is_nonprofit = fields.Boolean(
        string="Nonprofit Organization",
        default=False,
    )

    # Legal/Compliance Dates
    code_of_conduct_signed_date = fields.Date(
        string="Code of Conduct Signed On"
    )
    privacy_agreement_signed_date = fields.Date(
        string="Privacy Agreement Signed On"
    )

    # -> this will not be needed in the future, when contacts are linked to users (TODO)
    email_econgood = fields.Char(
        string="ECOnGOOD Email Address"
    )

    @api.constrains("employee_count", "inhabitant_count")
    def _check_non_negative_counts(self):
        for partner in self:
            if partner.employee_count < 0:
                raise ValidationError(
                    _("Number of Employees cannot be negative.")
                )
            if partner.inhabitant_count < 0:
                raise ValidationError(
                    _("Number of Inhabitants cannot be negative.")
                )

    @api.constrains("code_of_conduct_signed_date", "privacy_agreement_signed_date")
    def _check_signed_dates(self):
        today = fields.Date.context_today(self)
        for partner in self:
            if (
                partner.code_of_conduct_signed_date
                and partner.code_of_conduct_signed_date > today
            ):
                raise ValidationError(
                    _("Code of Conduct Signed On cannot be in the future.")
                )
            if (
                partner.privacy_agreement_signed_date
                and partner.privacy_agreement_signed_date > today
            ):
                raise ValidationError(
                    _("Privacy Agreement Signed On cannot be in the future.")
                )

    @api.constrains("email_econgood")
    def _check_email_econgood(self):
        for partner in self:
            if partner.email_econgood and not EMAIL_REGEX.match(
                partner.email_econgood.strip()
            ):
                raise ValidationError(
                    _("ECOnGOOD Email Address is not a valid email.")
                )
