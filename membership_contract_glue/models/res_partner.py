from datetime import date

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    def _get_next_january_first(self):
        self.ensure_one()
        today = fields.Date.to_date(fields.Date.context_today(self))
        jan_first_this_year = date(today.year, 1, 1)
        if today <= jan_first_this_year:
            return jan_first_this_year
        return date(today.year + 1, 1, 1)

    def action_open_create_membership_contract(self):
        self.ensure_one()
        current_company = self.env.company

        action = {
            "type": "ir.actions.act_window",
            "name": self.env._("Create Membership Contract"),
            "res_model": "contract.contract",
            "view_mode": "form",
            "view_id": self.env.ref("contract.contract_contract_customer_form_view").id,
            "target": "new",
            "context": {
                "default_name": f"{self.display_name} - {current_company.display_name}",
                "default_partner_id": self.id,
                "default_invoice_partner_id": self.id,
                "default_company_id": current_company.id,
                "default_contract_type": "sale",
                "default_is_membership_contract": True,
            },
        }

        if current_company.membership_contract_yearly_defaults:
            action["context"].update(
                {
                    "default_line_recurrence": False,
                    "default_recurring_interval": 1,
                    "default_recurring_rule_type": "yearly",
                    "default_recurring_next_date": self._get_next_january_first(),
                }
            )

        return action
