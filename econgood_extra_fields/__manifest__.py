# __manifest__.py
{
    'name': 'ECOnGOOD Extra Fields',
    'version': '18.0.2.5.0',
    'category': 'ECOnGOOD',
    'summary': 'Adds extra required fields (incl. dependencies) to contacts.',
    'author': 'ECOnGOOD',
    'depends': [
        'base',
        'contacts',
        'association_membership',
        'partner_company_type',  # Depends on OCA module
        'partner_contact_gender',  # Depends on OCA module
        'partner_contact_birthdate',  # Depends on OCA module
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/partner_taxonomy_data.xml',
        'views/res_partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'AGPL-3',
}
