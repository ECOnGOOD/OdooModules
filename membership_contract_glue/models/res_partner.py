from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResPartner(models.Model):
    _inherit = "res.partner"

    membership_contract_id = fields.Many2one(
        comodel_name="contract.contract",
        string="Membership Contract",
        copy=False,
    )
    membership_contract_line_ids = fields.One2many(
        related="membership_contract_id.contract_line_ids",
        readonly=False,
        string="Membership Contract Lines",
    )
    membership_contract_company_id = fields.Many2one(
        related="membership_contract_id.company_id",
        readonly=True,
    )
    membership_contract_date_start = fields.Date(
        related="membership_contract_id.date_start",
        readonly=True,
    )
    membership_contract_date_end = fields.Date(
        related="membership_contract_id.date_end",
        readonly=True,
    )
    membership_contract_next_invoice_date = fields.Date(
        related="membership_contract_id.recurring_next_date",
        readonly=True,
    )

    def _get_membership_company(self):
        self.ensure_one()
        return self.company_id or self.commercial_partner_id.company_id

    @api.constrains("membership_contract_id", "company_id")
    def _check_membership_contract_link(self):
        for partner in self.filtered("membership_contract_id"):
            contract = partner.membership_contract_id
            if not contract.is_membership_contract:
                raise ValidationError(
                    self.env._(
                        "The selected contract is not marked as a membership contract."
                    )
                )
            if contract.partner_id != partner:
                raise ValidationError(
                    self.env._(
                        "The membership contract partner '%(contract_partner)s' must "
                        "match '%(partner)s'."
                    )
                    % {
                        "contract_partner": contract.partner_id.display_name,
                        "partner": partner.display_name,
                    }
                )
            partner_company = partner._get_membership_company()
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
