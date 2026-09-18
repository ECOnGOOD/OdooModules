from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    membership_ok = fields.Boolean(string="Membership Product", index=True)
    membership_partner_type = fields.Selection(
        [
            ("any", "Individuals and Organisations"),
            ("person", "Individuals"),
            ("company", "Organisations"),
        ],
        string="Membership For",
        default="any",
        required=True,
    )


class ProductProduct(models.Model):
    _inherit = "product.product"

    @api.model
    def _membership_product_domain(self, company, partner=False):
        """Products a membership of ``company`` (and ``partner``) may use.

        Exactly this company or no company: a branch does not see its parent's
        products. Archived tiers are excluded by ``search`` itself.
        """
        domain = [
            ("membership_ok", "=", True),
            ("company_id", "in", [company.id, False]),
        ]
        if partner:
            partner_type = "company" if partner.is_company else "person"
            domain.append(("membership_partner_type", "in", ["any", partner_type]))
        return domain

    def _get_membership_price(self, company):
        """Single source for membership prices.

        ``lst_price`` includes the variant's ``price_extra`` (tier price), and
        stays correct if OCA ``product_variant_sale_price`` is installed.
        """
        if not self:
            return 0.0
        self.ensure_one()
        return self.with_company(company).lst_price
