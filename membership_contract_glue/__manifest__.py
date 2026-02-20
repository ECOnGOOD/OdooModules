{
    "name": "Membership Contract Glue",
    "version": "18.0.1.0.5",
    "category": "Membership",
    "summary": "Bridge membership management with recurring contracts",
    "author": "ECOnGOOD",
    "license": "AGPL-3",
    "depends": [
        "membership_extension",
        "contract",
    ],
    "data": [
        "security/membership_security.xml",
        "security/ir.model.access.csv",
        "views/res_company_views.xml",
        "views/contract_views.xml",
        "views/res_partner_views.xml",
    ],
    "installable": True,
    "application": False,
}
