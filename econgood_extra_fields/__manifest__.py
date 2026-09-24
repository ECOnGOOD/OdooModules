# __manifest__.py
{
    'name': 'ECOnGOOD Extra Fields',
    'version': '18.0.2.9.0',
    'category': 'ECOnGOOD',
    'summary': 'Adds extra required fields (incl. dependencies) to contacts.',
    'author': 'ECOnGOOD',
    'depends': [
        'base',
        'contacts',
        'account',
        'account_payment_mode',
        'account_payment_partner',  # customer_payment_mode_id on res.partner (was transitive via association_membership)
        'partner_multi_company',  # company_ids on res.partner (previously relied on co-installation)
        'partner_company_type',  # Depends on OCA module
        'partner_contact_gender',  # Depends on OCA module
        'partner_contact_birthdate',  # Depends on OCA module
        'partner_contact_address_default',  # partner_delivery_id hidden in res_partner_views.xml
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
