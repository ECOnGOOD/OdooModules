# __manifest__.py
{
    'name': 'ECOnGOOD Relationships',
    'version': '18.0.1.3.0',
    'category': 'ECOnGOOD',
    'summary': 'ECOnGOOD specifics of partner relations: members per local chapter.',
    'author': 'ECOnGOOD',
    'depends': [
        'econgood_extra_fields',  # is_econgood_ou, ou_type_id, company_ids on partners
        'association_membership',  # the report sits in Membership > Reporting
        'partner_multi_relation',  # res.partner.relation
    ],
    'data': [
        'security/ir.model.access.csv',
        'security/econgood_relationships_security.xml',
        'report/chapter_member_report_views.xml',
    ],
    'installable': True,
    'application': False,
    'license': 'AGPL-3',
}
