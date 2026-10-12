from odoo import _, api, fields, models
from odoo.exceptions import AccessError


class MembershipSettings(models.TransientModel):
    """The association's own settings, for Membership Managers (D59).

    Odoo lets only administrators save General Settings or write a company, and
    both rights would let a manager give themselves every company. This form
    holds exactly the company fields an association manages itself; the record
    is created as superuser so that its related fields may write the company.
    The access row (managers) and the allowed companies are the limit.
    """

    _name = "membership.settings"
    _description = "Association Settings"

    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        default=lambda self: self.env.company,
    )
    company_name = fields.Char(related="company_id.name", string="Name")
    company_country_id = fields.Many2one(related="company_id.country_id", string="Country")

    # Master data. Name, country, tax ID and registry stay with administrators:
    # the webform matches the association by name, German receipts follow the
    # country, and the identifiers are legal data.
    logo = fields.Binary(related="company_id.logo", readonly=False)
    street = fields.Char(related="company_id.street", readonly=False)
    street2 = fields.Char(related="company_id.street2", readonly=False)
    zip = fields.Char(related="company_id.zip", readonly=False)
    city = fields.Char(related="company_id.city", readonly=False)
    state_id = fields.Many2one(related="company_id.state_id", readonly=False)
    email = fields.Char(related="company_id.email", readonly=False)
    phone = fields.Char(related="company_id.phone", readonly=False)

    # Invoices and documents: what Odoo's document layout wizard edits.
    external_report_layout_id = fields.Many2one(
        related="company_id.external_report_layout_id", readonly=False
    )
    report_header = fields.Html(related="company_id.report_header", readonly=False)
    report_footer = fields.Html(related="company_id.report_footer", readonly=False)
    company_details = fields.Html(related="company_id.company_details", readonly=False)
    primary_color = fields.Char(related="company_id.primary_color", readonly=False)
    secondary_color = fields.Char(related="company_id.secondary_color", readonly=False)
    font = fields.Selection(related="company_id.font", readonly=False)
    layout_background = fields.Selection(related="company_id.layout_background", readonly=False)
    layout_background_image = fields.Binary(
        related="company_id.layout_background_image", readonly=False
    )
    terms_type = fields.Selection(related="company_id.terms_type", readonly=False)
    invoice_terms = fields.Html(related="company_id.invoice_terms", readonly=False)

    # Membership
    membership_company_mail_recipients = fields.Selection(
        related="company_id.membership_company_mail_recipients",
        readonly=False,
    )
    membership_default_period_year = fields.Integer(
        related="company_id.membership_default_period_year",
        readonly=False,
    )
    membership_invoicing_strategy = fields.Selection(
        related="company_id.membership_invoicing_strategy",
        readonly=False,
    )
    membership_activation_invoice_template_id = fields.Many2one(
        related="company_id.membership_activation_invoice_template_id",
        readonly=False,
    )
    membership_welcome_template_id = fields.Many2one(
        related="company_id.membership_welcome_template_id",
        readonly=False,
    )
    membership_cancellation_template_id = fields.Many2one(
        related="company_id.membership_cancellation_template_id",
        readonly=False,
    )
    membership_activation_invoice_org_template_id = fields.Many2one(
        related="company_id.membership_activation_invoice_org_template_id",
        readonly=False,
    )
    membership_welcome_org_template_id = fields.Many2one(
        related="company_id.membership_welcome_org_template_id",
        readonly=False,
    )
    membership_cancellation_org_template_id = fields.Many2one(
        related="company_id.membership_cancellation_org_template_id",
        readonly=False,
    )
    membership_tax_receipt_template_id = fields.Many2one(
        related="company_id.membership_tax_receipt_template_id",
        readonly=False,
    )
    member_number_own_sequence = fields.Boolean(
        related="company_id.member_number_own_sequence",
        readonly=False,
    )
    # The format of the sequence the company numbers from. Editable only with own
    # numbering; the default numbering is changed on its sequence by an administrator.
    member_number_prefix = fields.Char(
        string="Prefix",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_suffix = fields.Char(
        string="Suffix",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_padding = fields.Integer(
        string="Digits",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_next = fields.Integer(
        string="Next Number",
        compute="_compute_member_number_format",
        inverse="_inverse_member_number_format",
    )
    member_number_preview = fields.Char(
        string="Next Member Number",
        compute="_compute_member_number_preview",
    )

    @api.depends("company_id", "member_number_own_sequence")
    def _compute_member_number_format(self):
        for record in self:
            sequence = record.company_id._get_membership_number_sequence()
            record.member_number_prefix = sequence.prefix
            record.member_number_suffix = sequence.suffix
            record.member_number_padding = sequence.padding
            record.member_number_next = sequence.number_next_actual

    def _inverse_member_number_format(self):
        for record in self:
            if not record.member_number_own_sequence:
                continue
            # Also covers own numbering switched on in the same save.
            record.company_id._ensure_own_membership_number_sequence().write(
                {
                    "prefix": record.member_number_prefix,
                    "suffix": record.member_number_suffix,
                    "padding": record.member_number_padding,
                    "number_next": record.member_number_next,
                }
            )

    @api.depends(
        "member_number_prefix",
        "member_number_suffix",
        "member_number_padding",
        "member_number_next",
    )
    def _compute_member_number_preview(self):
        sequence_model = self.env["ir.sequence"]
        for record in self:
            # An unsaved sequence formats the values as they stand in the form.
            draft = sequence_model.new(
                {
                    "prefix": record.member_number_prefix,
                    "suffix": record.member_number_suffix,
                    "padding": record.member_number_padding,
                }
            )
            try:
                record.member_number_preview = draft.get_next_char(record.member_number_next)
            except Exception:
                record.member_number_preview = _("Invalid format")

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        allowed = self.env.user.company_ids
        for vals in vals_list:
            company = self.env["res.company"].browse(vals.get("company_id") or self.env.company.id)
            if company not in allowed:
                raise AccessError(_("You cannot change the settings of %s.", company.display_name))
            vals["company_id"] = company.id
        return super(MembershipSettings, self.sudo()).create(vals_list).sudo(False)

    def write(self, vals):
        # A second save of the same form; the company cannot change.
        self.check_access("write")
        vals.pop("company_id", None)
        return super(MembershipSettings, self.sudo()).write(vals)

    def action_save(self):
        # The web client saves the record before calling this.
        return {"type": "ir.actions.client", "tag": "soft_reload"}
