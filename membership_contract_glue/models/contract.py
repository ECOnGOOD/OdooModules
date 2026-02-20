from datetime import date

from odoo import api, fields, models


class ContractContract(models.Model):
    _inherit = "contract.contract"

    is_membership_contract = fields.Boolean(
        string="Membership Contract",
        index=True,
        copy=False,
        help="Flag this contract as the recurring billing source for membership.",
    )

    @api.model
    def _membership_next_jan_first(self):
        today = fields.Date.to_date(fields.Date.context_today(self))
        jan_first = date(today.year, 1, 1)
        if today <= jan_first:
            return jan_first
        return date(today.year + 1, 1, 1)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if not self.env.context.get("create_membership_contract_from_partner"):
            return defaults

        company = self.env.company
        defaults["company_id"] = company.id
        defaults["contract_type"] = "sale"
        defaults["is_membership_contract"] = True

        partner_id = defaults.get("partner_id") or self.env.context.get(
            "default_partner_id"
        )
        if partner_id:
            partner = self.env["res.partner"].browse(partner_id)
            defaults["name"] = f"{partner.display_name} - {company.display_name}"

        if company.membership_contract_yearly_defaults:
            defaults["line_recurrence"] = False
            defaults["recurring_interval"] = 1
            defaults["recurring_rule_type"] = "yearly"
            defaults["recurring_next_date"] = self._membership_next_jan_first()

        return defaults
