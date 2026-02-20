from odoo import api, fields, models


class ContractLine(models.Model):
    _inherit = "contract.line"

    @api.model
    def _get_membership_default_date_end(self, date_start):
        date_start = fields.Date.to_date(date_start)
        return date_start.replace(month=12, day=31)

    def _should_default_membership_date_end(self):
        self.ensure_one()
        return bool(
            self.contract_id.is_membership_contract
            and self.contract_id.company_id.membership_contract_dec31_default
        )

    @api.onchange("contract_id", "date_start")
    def _onchange_membership_default_date_end(self):
        for line in self.filtered(lambda l: l.date_start and not l.date_end):
            if line._should_default_membership_date_end():
                line.date_end = line._get_membership_default_date_end(line.date_start)

    @api.model_create_multi
    def create(self, vals_list):
        default_contract_id = self.env.context.get("default_contract_id")
        for vals in vals_list:
            if vals.get("date_end") or not vals.get("date_start"):
                continue
            contract_id = vals.get("contract_id") or default_contract_id
            if not contract_id:
                continue
            contract = self.env["contract.contract"].browse(contract_id)
            if (
                contract
                and contract.is_membership_contract
                and contract.company_id.membership_contract_dec31_default
            ):
                vals["date_end"] = self._get_membership_default_date_end(
                    vals["date_start"]
                )
        return super().create(vals_list)
