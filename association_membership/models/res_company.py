from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


MEMBERSHIP_NUMBER_SEQUENCE_CODE = "association.membership.number.seq"
# The default numbering (D31); administrators change it on the sequence itself.
DEFAULT_MEMBER_NUMBER_FORMAT = {
    "prefix": "MEM/%(year)s/",
    "suffix": False,
    "padding": 5,
}

INVOICING_STRATEGY_SELECTION = [
    ("manual", "Manual"),
    ("draft", "Draft"),
    ("confirm", "Confirm"),
]


def normalize_year_value(value, field_label):
    normalized = str(value or "").replace(",", "").strip()
    if not normalized:
        raise ValidationError(_("%(field)s is required.") % {"field": field_label})
    if not normalized.isdigit():
        raise ValidationError(_("%(field)s must be a whole number.") % {"field": field_label})
    year = int(normalized)
    if year <= 0:
        raise ValidationError(_("%(field)s must be greater than zero.") % {"field": field_label})
    return year


class ResCompany(models.Model):
    _inherit = "res.company"

    membership_company_mail_recipients = fields.Selection(
        [
            ("member", "The organisation"),
            ("contact_person", "Contact person"),
            ("invoice_contact", "Invoice contact"),
            ("contact_person_and_invoice_contact", "Contact person and invoice contact"),
        ],
        string="Email recipients for organisation members",
        default="contact_person",
        required=True,
        help="Individuals always receive their own emails.",
    )
    membership_default_period_year = fields.Integer(
        string="Period Year Override",
        default=0,
        help="Leave 0 to always default new periods to the current year. "
             "Set a future year to default new periods to that year, "
             "e.g. for early renewals. Past years always fall back to the current year.",
    )
    membership_invoicing_strategy = fields.Selection(
        selection=INVOICING_STRATEGY_SELECTION,
        string="Invoicing Strategy",
        default="manual",
        required=True,
        help="Default for new memberships of this company; a membership may override it.\n"
             "Manual: no invoice is created automatically. Record payments with"
             " \"Mark as Paid\", or create an invoice by hand when one is needed.\n"
             "Draft: activation and renewal create a draft invoice.\n"
             "Confirm: activation and renewal create and post the invoice.",
    )
    membership_activation_invoice_template_id = fields.Many2one(
        "mail.template",
        string="Activation Invoice Email Template",
        default=lambda self: self.env.ref(
            "association_membership.mail_template_membership_activation_invoice",
            raise_if_not_found=False,
        ),
    )
    membership_welcome_template_id = fields.Many2one(
        "mail.template",
        string="Welcome Email Template",
        default=lambda self: self.env.ref(
            "association_membership.mail_template_membership_welcome",
            raise_if_not_found=False,
        ),
    )
    membership_cancellation_template_id = fields.Many2one(
        "mail.template",
        string="Cancellation Email Template",
        default=lambda self: self.env.ref(
            "association_membership.mail_template_membership_cancellation",
            raise_if_not_found=False,
        ),
    )
    # The whole member number is an ir.sequence (15.22): the default one without
    # company, or the company's own with the same code.
    member_number_own_sequence = fields.Boolean(
        string="Own Member Numbering",
        compute="_compute_member_number_own_sequence",
        inverse="_inverse_member_number_own_sequence",
        help=(
            "Off (recommended): the company uses the default numbering, one counter "
            "shared by all companies, so member numbers cannot collide. On: the "
            "company numbers on its own, with its own format and counter, starting "
            "where the default counter stands. Its prefix must then differ from "
            "every other company's."
        ),
    )

    def _membership_period_year(self):
        self.ensure_one()
        current_year = fields.Date.today().year
        override = self.membership_default_period_year
        return override if override and override >= current_year else current_year

    @api.constrains(
        "membership_default_period_year",
        "membership_activation_invoice_template_id",
        "membership_welcome_template_id",
        "membership_cancellation_template_id",
    )
    def _check_member_number_settings(self):
        for company in self:
            if company.membership_default_period_year:
                normalize_year_value(
                    company.membership_default_period_year,
                    company._fields["membership_default_period_year"].string,
                )
            company._check_membership_mail_template_model(
                company.membership_activation_invoice_template_id,
                "account.move",
            )
            company._check_membership_mail_template_model(
                company.membership_welcome_template_id,
                "membership.membership",
            )
            company._check_membership_mail_template_model(
                company.membership_cancellation_template_id,
                "membership.membership",
            )

    def _default_membership_number_sequence(self):
        """The default numbering, shared by every company without its own.

        Shipped as data; created lazily so the numbering cannot break on a
        database where the record was removed.
        """
        sequence_model = self.env["ir.sequence"].sudo()
        default = sequence_model.search(
            [("code", "=", MEMBERSHIP_NUMBER_SEQUENCE_CODE), ("company_id", "=", False)],
            limit=1,
        )
        if not default:
            default = sequence_model.create(
                {
                    "name": "Member Number (default)",
                    "code": MEMBERSHIP_NUMBER_SEQUENCE_CODE,
                    "company_id": False,
                    # Gaps matter here: a rolled-back signup must not use up a number.
                    "implementation": "no_gap",
                    **DEFAULT_MEMBER_NUMBER_FORMAT,
                }
            )
        return default

    def _own_membership_number_sequence(self, active_test=True):
        self.ensure_one()
        return (
            self.env["ir.sequence"]
            .sudo()
            .with_context(active_test=active_test)
            .search(
                [
                    ("code", "=", MEMBERSHIP_NUMBER_SEQUENCE_CODE),
                    ("company_id", "=", self.id),
                ],
                limit=1,
            )
        )

    def _get_membership_number_sequence(self):
        """The sequence the company's next member number comes from."""
        self.ensure_one()
        return self._own_membership_number_sequence() or self._default_membership_number_sequence()

    def _ensure_own_membership_number_sequence(self):
        """Switch the company to own numbering; returns its sequence.

        An archived one is reactivated. A new one copies the default format.
        Either way the counter does not stand behind the default one, whose
        numbers below that are already issued.
        """
        self.ensure_one()
        default = self._default_membership_number_sequence()
        sequence = self._own_membership_number_sequence(active_test=False)
        if sequence:
            vals = {"active": True}
            # The default counter moved on meanwhile; with the same prefix its
            # numbers would come round again.
            if sequence.number_next_actual < default.number_next_actual:
                vals["number_next"] = default.number_next_actual
            sequence.write(vals)
            return sequence
        return default.copy(
            {
                "name": "Member Number (%s)" % self.name,
                "company_id": self.id,
                "implementation": "no_gap",
                "number_next": default.number_next_actual,
            }
        )

    def _compute_member_number_own_sequence(self):
        for company in self:
            company.member_number_own_sequence = bool(
                company.id and company._own_membership_number_sequence()
            )

    def _inverse_member_number_own_sequence(self):
        for company in self:
            if company.member_number_own_sequence:
                company._ensure_own_membership_number_sequence()
            else:
                company._own_membership_number_sequence().active = False

    def _ensure_tax_receipt_sequence(self):
        """Give the company its own receipt numbering.

        donation_base creates one sequence for the company that installs it;
        every association numbers its own receipts.
        """
        self.ensure_one()
        sequence_model = self.env["ir.sequence"].sudo()
        code = "donation.tax.receipt"
        if sequence_model.search_count([("code", "=", code), ("company_id", "in", [self.id, False])]):
            return
        sequence_model.create(
            {
                "name": "Donation Tax Receipt (%s)" % self.name,
                "code": code,
                "company_id": self.id,
                "prefix": "%(range_year)s-",
                "use_date_range": True,
                "padding": 5,
            }
        )

    def _check_membership_mail_template_model(self, template, expected_model):
        self.ensure_one()
        if not template:
            return
        actual_model = template.model_id.model or template.model
        if actual_model != expected_model:
            raise ValidationError(
                _(
                    "%(template)s must use model %(model)s."
                )
                % {
                    "template": template.display_name,
                    "model": expected_model,
                }
            )
