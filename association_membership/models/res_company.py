from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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

    membership_product_category_id = fields.Many2one(
        "product.category",
        string="Membership Product Category",
        default=lambda self: self._default_membership_product_category(),
    )
    membership_auto_activate_on_payment = fields.Boolean(
        string="Auto-activate membership on payment",
        default=False,
    )
    membership_cron_year_offset = fields.Integer(
        string="Renewal Year Offset",
        default=1,
    )
    membership_default_contribution_year = fields.Integer(
        string="Default Contribution Year",
        default=lambda self: fields.Date.today().year,
    )
    membership_invoicing_strategy = fields.Selection(
        selection=INVOICING_STRATEGY_SELECTION,
        string="Invoicing Strategy",
        default="draft",
        required=True,
    )
    membership_activation_invoice_template_id = fields.Many2one(
        "mail.template",
        string="Activation Invoice Email Template",
    )
    membership_welcome_template_id = fields.Many2one(
        "mail.template",
        string="Welcome Email Template",
    )
    membership_cancellation_template_id = fields.Many2one(
        "mail.template",
        string="Cancellation Email Template",
    )
    member_number_prefix = fields.Char(
        string="Member Number Prefix",
        default="MEM/%(year)s/",
    )
    member_number_padding = fields.Integer(
        string="Member Number Padding",
        default=5,
    )

    @api.model
    def _default_membership_product_category(self):
        return self.env.ref(
            "association_membership.product_category_membership",
            raise_if_not_found=False,
        )

    def _membership_product_category(self):
        self.ensure_one()
        return self.membership_product_category_id or self._default_membership_product_category()

    def _membership_cron_target_year(self):
        self.ensure_one()
        return fields.Date.today().year + (self.membership_cron_year_offset or 1)

    @api.constrains(
        "member_number_padding",
        "member_number_prefix",
        "membership_default_contribution_year",
        "membership_activation_invoice_template_id",
        "membership_welcome_template_id",
        "membership_cancellation_template_id",
    )
    def _check_member_number_settings(self):
        for company in self:
            if company.member_number_padding <= 0:
                raise ValidationError(_("Member Number Padding must be greater than zero."))
            normalize_year_value(
                company.membership_default_contribution_year,
                company._fields["membership_default_contribution_year"].string,
            )
            try:
                company._render_member_number_prefix()
            except Exception as error:
                raise ValidationError(
                    _(
                        "Invalid Member Number Prefix '%(prefix)s'."
                    )
                    % {"prefix": company.member_number_prefix}
                ) from error
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

    def _get_membership_number_sequence(self):
        self.ensure_one()
        code = "association.membership.number.seq"
        sequence = self.env["ir.sequence"].sudo().search(
            [("code", "=", code), ("company_id", "=", self.id)], limit=1
        )
        if not sequence:
            global_seq = self.env["ir.sequence"].sudo().search(
                [("code", "=", code), ("company_id", "=", False)], limit=1
            )
            sequence = self.env["ir.sequence"].sudo().create(
                {
                    "name": "Membership Number Counter (%s)" % self.name,
                    "code": code,
                    "company_id": self.id,
                    "padding": global_seq.padding if global_seq else 1,
                    "number_next": global_seq.number_next_actual if global_seq else 1,
                }
            )
        return sequence

    def _render_member_number_prefix(self, target_date=False):
        self.ensure_one()
        sequence_date = fields.Date.to_date(target_date or fields.Date.today())
        prefix = self.member_number_prefix or ""
        return prefix % {"year": sequence_date.year}

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
