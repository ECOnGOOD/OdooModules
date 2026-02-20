from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ContractContract(models.Model):
    _inherit = "contract.contract"

    is_membership_contract = fields.Boolean(
        string="Membership Contract",
        index=True,
        copy=False,
        help="Flag this contract as the recurring billing source for membership.",
    )

    @api.onchange("partner_id", "is_membership_contract")
    def _onchange_membership_company(self):
        for contract in self.filtered("is_membership_contract"):
            partner_company = contract._get_membership_partner_company()
            if partner_company:
                contract.company_id = partner_company

    def _get_membership_partner_company(self):
        self.ensure_one()
        return (
            self.partner_id.company_id
            or self.partner_id.commercial_partner_id.company_id
        )

    @api.constrains("is_membership_contract", "partner_id", "company_id")
    def _check_membership_company_consistency(self):
        for contract in self.filtered("is_membership_contract"):
            partner_company = contract._get_membership_partner_company()
            if not partner_company:
                raise ValidationError(
                    self.env._(
                        "Membership contracts require a company on the member "
                        "(contact or commercial entity)."
                    )
                )
            if contract.company_id != partner_company:
                raise ValidationError(
                    self.env._(
                        "Membership contract company '%(contract_company)s' must "
                        "match member company '%(partner_company)s'."
                    )
                    % {
                        "contract_company": contract.company_id.display_name,
                        "partner_company": partner_company.display_name,
                    }
                )
