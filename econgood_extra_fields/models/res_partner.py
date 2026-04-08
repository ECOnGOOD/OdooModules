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

    # Organization classification
    x_is_econgood_ou = fields.Boolean(
        string="ECOnGOOD OU",
        help="Enable this for ECOnGOOD organizational units such as national, regional, local chapter, or hub records.",
        default=False,
    )
    x_organization_kind_id = fields.Many2one(
        comodel_name="res.partner.organization.kind",
        string="Organization Kind",
    )
    x_ou_type_id = fields.Many2one(
        comodel_name="res.partner.ou.type",
        string="OU Type",
    )
    x_nonprofit_status = fields.Selection(
        selection=[
            ("unknown", "Unknown"),
            ("confirmed", "Confirmed nonprofit"),
            ("not_nonprofit", "Not nonprofit"),
        ],
        string="Nonprofit Status",
        default="unknown",
        required=True,
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

    @api.onchange("company_type")
    def _onchange_company_type_clear_org_fields_for_people(self):
        for partner in self:
            if partner.company_type == "person":
                partner.x_is_econgood_ou = False
                partner.x_organization_kind_id = False
                partner.x_ou_type_id = False

    @api.onchange("x_is_econgood_ou")
    def _onchange_x_is_econgood_ou(self):
        for partner in self:
            if partner.x_is_econgood_ou:
                partner.x_organization_kind_id = False
            else:
                partner.x_ou_type_id = False

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

    @api.constrains(
        "company_type",
        "x_is_econgood_ou",
        "x_organization_kind_id",
        "x_ou_type_id",
    )
    def _check_company_classification_fields(self):
        for partner in self:
            if partner.company_type == "person" and (
                partner.x_is_econgood_ou
                or partner.x_organization_kind_id
                or partner.x_ou_type_id
            ):
                raise ValidationError(
                    _(
                        "ECOnGOOD OU, Organization Kind, and OU Type can only be set on company contacts."
                    )
                )
            if partner.x_is_econgood_ou and partner.x_organization_kind_id:
                raise ValidationError(
                    _("Organization Kind must be empty when ECOnGOOD OU is enabled.")
                )
            if not partner.x_is_econgood_ou and partner.x_ou_type_id:
                raise ValidationError(
                    _("OU Type requires ECOnGOOD OU to be enabled.")
                )
