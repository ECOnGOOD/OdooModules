from odoo import api, fields, models


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_membership_product = fields.Boolean(
        compute="_compute_is_membership_product",
        search="_search_is_membership_product",
    )

    @api.model
    def _get_membership_category(self, company=False):
        company = company or self.env.company
        return company._membership_product_category()

    def _compute_is_membership_product(self):
        category = self._get_membership_category()
        allowed_ids = set(
            self.env["product.category"]
            .search([("id", "child_of", category.id)])
            .ids
        ) if category else set()
        for record in self:
            record.is_membership_product = bool(record.categ_id.id in allowed_ids)

    @api.model
    def _search_is_membership_product(self, operator, value):
        category = self._get_membership_category()
        if not category:
            return [("id", "=", 0)] if value else []
        domain = [("categ_id", "child_of", category.id)]
        if operator in ("=", "=="):
            return domain if bool(value) else ["!", *domain]
        if operator in ("!=", "<>"):
            return ["!", *domain] if bool(value) else domain
        return [("id", "=", 0)]


class ProductProduct(models.Model):
    _inherit = "product.product"

    is_membership_product = fields.Boolean(
        related="product_tmpl_id.is_membership_product",
        store=False,
    )

    @api.model
    def _get_membership_category(self, company=False):
        return self.env["product.template"]._get_membership_category(company=company)
