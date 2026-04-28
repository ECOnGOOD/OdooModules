{
    "name": "Association Membership — Germany",
    "version": "18.0.1.0.0",
    "category": "Association",
    "summary": "Zuwendungsbestätigung (German tax-deductible donation receipt) for memberships",
    "author": "ECOnGOOD",
    "license": "AGPL-3",
    "depends": [
        "association_membership",
        "donation_base",
    ],
    "external_dependencies": {
        "python": ["num2words"],
    },
    "data": [
        "views/res_config_settings_views.xml",
        "views/donation_tax_receipt_views.xml",
        "report/zuwendungsbestaetigung_report.xml",
        "report/zuwendungsbestaetigung_template.xml",
    ],
    "installable": True,
    "application": False,
}
