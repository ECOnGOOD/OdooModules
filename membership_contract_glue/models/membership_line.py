from odoo import api, models
from odoo.exceptions import ValidationError


class MembershipLine(models.Model):
    _inherit = "membership.membership_line"

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            partner_id = vals.get("partner") or vals.get("partner_id")
            if not partner_id:
                continue
            partner = self.env["res.partner"].browse(partner_id)
            contract = partner.membership_contract_id
            if contract and not vals.get("company_id"):
                vals["company_id"] = contract.company_id.id
        return super().create(vals_list)

    @api.constrains("partner", "company_id")
    def _check_membership_line_company_matches_contract(self):
        lines = self.filtered(
            lambda rec: rec.partner and rec.partner.membership_contract_id
        )
        for line in lines:
            contract = line.partner.membership_contract_id
            if line.company_id != contract.company_id:
                raise ValidationError(
                    self.env._(
                        "Membership line company '%(line_company)s' must match "
                        "membership contract company '%(contract_company)s'."
                    )
                    % {
                        "line_company": line.company_id.display_name,
                        "contract_company": contract.company_id.display_name,
                    }
                )
