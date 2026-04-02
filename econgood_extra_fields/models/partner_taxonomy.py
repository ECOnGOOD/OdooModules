from odoo import fields, models


class ResPartnerOrganizationKind(models.Model):
    _name = "res.partner.organization.kind"
    _description = "Partner Organization Kind"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "org_kind_code_uniq",
            "unique(code)",
            "Organization kind code must be unique.",
        ),
    ]


class ResPartnerOuType(models.Model):
    _name = "res.partner.ou.type"
    _description = "Partner OU Type"
    _order = "sequence, name"

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        (
            "ou_type_code_uniq",
            "unique(code)",
            "OU type code must be unique.",
        ),
    ]
