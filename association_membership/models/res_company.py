from odoo import api, fields, models


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
