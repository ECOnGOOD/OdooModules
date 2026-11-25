import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Letter salutations by the association's style (D81), in German like the
# importers' values. A partner with any other salutation keeps it.
LETTER_SALUTATIONS = {
    "informal": {"female": "Liebe Frau", "male": "Lieber Herr"},
    "formal": {"female": "Sehr geehrte Frau", "male": "Sehr geehrter Herr"},
}
GENERATED_LETTER_SALUTATIONS = {
    salutation for by_gender in LETTER_SALUTATIONS.values() for salutation in by_gender.values()
}
GENDER_BY_TITLE = {
    "base.res_partner_title_madam": "female",
    "base.res_partner_title_miss": "female",
    "base.res_partner_title_mister": "male",
}


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

    # Organization classification
    is_econgood_ou = fields.Boolean(
        string="ECOnGOOD OU",
        tracking=True,
        help="Enable this for ECOnGOOD organizational units such as national, regional, local chapter, or hub records.",
        default=False,
    )
    organization_kind_id = fields.Many2one(
        comodel_name="res.partner.organization.kind",
        string="Organization Kind",
        tracking=True,
    )
    ou_type_id = fields.Many2one(
        comodel_name="res.partner.ou.type",
        string="OU Type",
        tracking=True,
    )
    nonprofit_status = fields.Selection(
        selection=[
            ("unknown", "Unknown"),
            ("confirmed", "Confirmed nonprofit"),
            ("not_nonprofit", "Not nonprofit"),
        ],
        string="Nonprofit Status",
        default="unknown",
        tracking=True,
        required=True,
    )

    is_municipality = fields.Boolean(
        compute="_compute_is_municipality",
        store=False,
    )

    # Legal/Compliance Dates
    code_of_conduct_signed_date = fields.Date(
        string="Code of Conduct Signed On",
    )
    privacy_agreement_signed_date = fields.Date(
        string="Privacy Agreement Signed On",
    )

    # -> this will not be needed in the future, when contacts are linked to users (TODO)
    email_econgood = fields.Char(
        string="ECOnGOOD Email Address",
    )

    legacy_id_smartwe = fields.Char(string="Legacy ID SmartWe")
    legacy_id_formidable = fields.Char(string="Legacy ID Formidable")
    letter_salutation = fields.Char(
        string="Letter Salutation",
        compute="_compute_letter_salutation",
        store=True,
        readonly=False,
        help="Filled from the gender in the style of the current association while"
        " empty or still filled that way; a salutation entered by hand is kept.",
    )
    socials = fields.Char(string="Socials")

    @api.depends("gender", "is_company")
    def _compute_letter_salutation(self):
        style = self.env.company.letter_salutation_style or "informal"
        for partner in self:
            current = partner.letter_salutation
            if current and current not in GENERATED_LETTER_SALUTATIONS:
                continue
            # The wording is German; other languages are not filled yet.
            if partner.is_company or not (partner.lang or "").startswith("de"):
                continue
            partner.letter_salutation = LETTER_SALUTATIONS[style].get(partner.gender) or False

    @api.onchange("title")
    def _onchange_title_set_gender(self):
        for partner in self:
            if partner.gender or not partner.title:
                continue
            for xmlid, gender in GENDER_BY_TITLE.items():
                if partner.title == self.env.ref(xmlid, raise_if_not_found=False):
                    partner.gender = gender
                    break

    @api.onchange("company_type")
    def _onchange_company_type_clear_org_fields_for_people(self):
        for partner in self:
            if partner.company_type == "person":
                partner.is_econgood_ou = False
                partner.organization_kind_id = False
                partner.ou_type_id = False

    @api.onchange("is_econgood_ou")
    def _onchange_is_econgood_ou(self):
        for partner in self:
            if partner.is_econgood_ou:
                partner.organization_kind_id = False
            else:
                partner.ou_type_id = False

    @api.depends("organization_kind_id")
    def _compute_is_municipality(self):
        for partner in self:
            partner.is_municipality = (
                partner.organization_kind_id 
                and partner.organization_kind_id.code == "municipality_public_body"
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

    @api.constrains(
        "code_of_conduct_signed_date",
        "privacy_agreement_signed_date",
    )
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
            if partner.email_econgood:
                if not EMAIL_REGEX.match(partner.email_econgood.strip()):
                    raise ValidationError(
                        _("ECOnGOOD Email Address is not a valid email.")
                    )
                if not partner.email_econgood.strip().lower().endswith("@econgood.org"):
                    raise ValidationError(
                        _("ECOnGOOD Email Address must end with @econgood.org")
                    )

    @api.constrains(
        "company_type",
        "is_econgood_ou",
        "organization_kind_id",
        "ou_type_id",
    )
    def _check_company_classification_fields(self):
        for partner in self:
            if partner.company_type == "person" and (
                partner.is_econgood_ou
                or partner.organization_kind_id
                or partner.ou_type_id
            ):
                raise ValidationError(
                    _(
                        "ECOnGOOD OU, Organization Kind, and OU Type can only be set on company contacts."
                    )
                )
            if partner.is_econgood_ou and partner.organization_kind_id:
                raise ValidationError(
                    _("Organization Kind must be empty when ECOnGOOD OU is enabled.")
                )
            if not partner.is_econgood_ou and partner.ou_type_id:
                raise ValidationError(
                    _("OU Type requires ECOnGOOD OU to be enabled.")
                )
