from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    membership_auto_activate_on_payment = fields.Boolean(
        related="company_id.membership_auto_activate_on_payment",
        readonly=False,
    )
    membership_product_category_id = fields.Many2one(
        related="company_id.membership_product_category_id",
        readonly=False,
    )
    membership_cron_year_offset = fields.Integer(
        related="company_id.membership_cron_year_offset",
        readonly=False,
    )
    membership_cron_auto_post = fields.Boolean(
        related="company_id.membership_cron_auto_post",
        readonly=False,
    )
