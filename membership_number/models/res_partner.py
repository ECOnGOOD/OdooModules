# models/res_partner.py
from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # The actual unique number per company
    association_member_number = fields.Char(
        string='Membership Number',
        company_dependent=True,
        copy=False
    )

    # A display field to show numbers from all companies/branches
    all_membership_numbers_display = fields.Char(
        string='All Association Numbers',
        compute='_compute_all_membership_numbers',
        help="Shows membership numbers across all regional/national branches."
    )

    def _compute_all_membership_numbers(self):
        for partner in self:
            # We search the 'ir.property' table for this partner's membership numbers
            properties = self.env['ir.property'].sudo().search([
                ('res_id', '=', f'res.partner,{partner.id}'),
                ('name', '=', 'association_member_number'),
                ('fields_id.model', '=', 'res.partner')
            ])
            
            # Extract the values and filter out empties
            numbers = [p.value_text for p in properties if p.value_text]
            
            # If the current company has a value not yet in the list (unsaved), add it
            current_val = partner.association_member_number
            if current_val and current_val not in numbers:
                numbers.append(current_val)
                
            partner.all_membership_numbers_display = " | ".join(numbers) if numbers else "None"

    @api.constrains('association_member_number')
    def _check_unique_member_number(self):
        for record in self:
            if not record.association_member_number:
                continue
            # Search for other partners in the CURRENT company with the same number
            domain = [
                ('association_member_number', '=', record.association_member_number),
                ('id', '!=', record.id)
                # Odoo automatically applies current company domain for company_dependent fields
            ]
            if self.search_count(domain) > 0:
                # Warning: Use standard ValidationError if you want to block saving
                # from odoo.exceptions import ValidationError
                # raise ValidationError("This Membership Number is already assigned to another contact in this association.")
                pass