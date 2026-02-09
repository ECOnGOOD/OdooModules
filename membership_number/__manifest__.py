# __manifest__.py
{
    'name': 'Membership Numbering',
    'version': '18.0.1.0.5',
    'category': 'Association',
    'summary': 'Adds auto-sequenced numbers to membership lines and displays them on members.',
    'author': 'Gabriel Geck',
    'depends': ['membership', 'base'],
    'data': [
        'data/ir_sequence_data.xml',
        'views/partner_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'AGPL-3',
}