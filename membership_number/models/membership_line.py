# models/membership_line.py
from odoo import api, models

class MembershipLine(models.Model):
    _inherit = "membership.membership_line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        sequence_model = self.env["ir.sequence"]

        for line in lines:
            partner = line.partner
            line_company = line.company_id or self.env.company
            partner_in_company = partner.with_company(line_company)
            if partner and not partner_in_company.association_member_number:
                new_number = sequence_model.with_company(line_company).next_by_code(
                    "association.membership.seq"
                )
                if new_number:
                    partner_in_company.association_member_number = new_number

        return lines
