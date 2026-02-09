# __manifest__.py
{
    'name': 'Membership Numbering 6',
    'version': '18.0.1.0.6',
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