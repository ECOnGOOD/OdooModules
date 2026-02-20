# __manifest__.py
{
    'name': 'Membership Numbering',
    'version': '18.0.2.0.0',
    'category': 'Association',
    'summary': 'Adds auto-sequenced member numbers to membership lines and displays them on contacts.',
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
