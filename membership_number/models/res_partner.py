# models/res_partner.py
from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # 1. This is the "Current Association" number (Editable)
    association_member_number = fields.Char(
        string='Membership Number',
        company_dependent=True,
        copy=False,
        help="Unique identifier for the member within this specific association (Company)."
    )

    # 2. This shows numbers from ALL associations (Read-only)
    all_membership_numbers_display = fields.Char(
        string='All Membership Numbers',
        compute='_compute_all_membership_numbers',
        help="Shows membership numbers from all regional and national branches."
    )

    @api.depends('association_member_number')  # Note: company_dependent triggers are tricky
    def _compute_all_membership_numbers(self):
        # We fetch all companies the user has access to
        all_companies = self.env['res.company'].search([])
        for partner in self:
            found_numbers = []
            for company in all_companies:
                # We "peek" into the value for each specific company
                val = partner.with_company(company).association_member_number
                if val:
                    found_numbers.append(f"{company.name}: {val}")
            
            partner.all_membership_numbers_display = " | ".join(found_numbers) if found_numbers else "No numbers assigned"

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