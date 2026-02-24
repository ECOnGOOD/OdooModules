import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ResPartner(models.Model):
    _inherit = "res.partner"

    # Demographics (specific to companies and/or Commune/Region partners)
    x_employee_count = fields.Integer(
        string="Number of Employees",
        help="Employee count (FTE).",
    )
    x_inhabitant_count = fields.Integer(
        string="Number of Inhabitants",
        help="Population count if this partner represents a region or commune.",
    )

    # Nonprofit Status
    x_is_nonprofit = fields.Boolean(
        string="Nonprofit Organization",
        default=False,
    )

    # Legal/Compliance Dates
    x_code_of_conduct_signed_date = fields.Date(
        string="Code of Conduct Signed On",
    )
    x_privacy_agreement_signed_date = fields.Date(
        string="Privacy Agreement Signed On",
    )

    # -> this will not be needed in the future, when contacts are linked to users (TODO)
    x_email_econgood = fields.Char(
        string="ECOnGOOD Email Address",
    )

    x_legacy_id_smartwe = fields.Char(string="Legacy ID SmartWe")
    x_legacy_id_formidable = fields.Char(string="Legacy ID Formidable")
    x_letter_salutation = fields.Char(string="Letter Salutation")
    x_socials = fields.Char(string="Socials")

    @api.constrains("x_employee_count", "x_inhabitant_count")
    def _check_non_negative_counts(self):
        for partner in self:
            if partner.x_employee_count < 0:
                raise ValidationError(
                    _("Number of Employees cannot be negative.")
                )
            if partner.x_inhabitant_count < 0:
                raise ValidationError(
                    _("Number of Inhabitants cannot be negative.")
                )

    @api.constrains(
        "x_code_of_conduct_signed_date",
        "x_privacy_agreement_signed_date",
    )
    def _check_signed_dates(self):
        today = fields.Date.context_today(self)
        for partner in self:
            if (
                partner.x_code_of_conduct_signed_date
                and partner.x_code_of_conduct_signed_date > today
            ):
                raise ValidationError(
                    _("Code of Conduct Signed On cannot be in the future.")
                )
            if (
                partner.x_privacy_agreement_signed_date
                and partner.x_privacy_agreement_signed_date > today
            ):
                raise ValidationError(
                    _("Privacy Agreement Signed On cannot be in the future.")
                )

    @api.constrains("x_email_econgood")
    def _check_x_email_econgood(self):
        for partner in self:
            if partner.x_email_econgood and not EMAIL_REGEX.match(
                partner.x_email_econgood.strip()
            ):
                raise ValidationError(
                    _("ECOnGOOD Email Address is not a valid email.")
                )
