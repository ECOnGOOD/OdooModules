from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
    membership_cron_auto_post = fields.Boolean(
        string="Auto-post invoices created by the scheduled renewal",
        default=False,
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

    @api.constrains("member_number_padding", "member_number_prefix")
    def _check_member_number_settings(self):
        for company in self:
            if company.member_number_padding <= 0:
                raise ValidationError(_("Member Number Padding must be greater than zero."))
            try:
                company._render_member_number_prefix()
            except Exception as error:
                raise ValidationError(
                    _(
                        "Invalid Member Number Prefix '%(prefix)s'."
                    )
                    % {"prefix": company.member_number_prefix}
                ) from error

    def _render_member_number_prefix(self, target_date=False):
        self.ensure_one()
        sequence_date = fields.Date.to_date(target_date or fields.Date.today())
        prefix = self.member_number_prefix or ""
        return prefix % {"year": sequence_date.year}
