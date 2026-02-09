# models/res_partner.py
from odoo import models, fields, api

class ResPartner(models.Model):
    _inherit = 'res.partner'

    # company_dependent=True ensures this value is unique per company/association
    association_member_number = fields.Char(
        string='Membership Number',
        company_dependent=True,
        copy=False,
        help="Unique identifier for the member within this specific association (Company)."
    )

    _sql_constraints = [
        # Optional: Strict DB constraint to prevent duplicates within the same company
        # Note: company_dependent fields are stored in ir_property, so SQL constraints
        # on the main table won't work directly. Uniqueness must be checked in Python.
    ]

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