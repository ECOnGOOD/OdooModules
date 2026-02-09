# models/membership_line.py
from odoo import models, api

class MembershipLine(models.Model):
    _inherit = 'membership.membership_line'

    @api.model_create_multi
    def create(self, vals_list):
        # Create the membership lines first
        lines = super(MembershipLine, self).create(vals_list)

        for line in lines:
            partner = line.partner
            
            # We only generate a number if:
            # 1. The partner doesn't have one for the current company
            # 2. We are in a company context
            if partner and not partner.association_member_number:
                # Odoo will use the sequence associated with the current company
                new_number = self.env['ir.sequence'].next_by_code('association.membership.seq')
                
                if new_number:
                    partner.association_member_number = new_number
        
        return lines