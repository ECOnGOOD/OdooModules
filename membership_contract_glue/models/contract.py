from datetime import date

from odoo import api, fields, models


class ContractContract(models.Model):
    _inherit = "contract.contract"

    is_membership_contract = fields.Boolean(
        string="Membership Contract",
        index=True,
        copy=False,
        help="Flag this contract as the recurring billing source for membership.",
    )

    @api.model
    def _membership_next_jan_first(self):
        today = fields.Date.to_date(fields.Date.context_today(self))
        jan_first = date(today.year, 1, 1)
        if today <= jan_first:
            return jan_first
        return date(today.year + 1, 1, 1)

    @api.model
    def _use_silent_membership_import(self):
        return bool(self.env.context.get("silent_membership_contract_import"))

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if not self.env.context.get("create_membership_contract_from_partner"):
            return defaults

        company = self.env.company
        defaults["company_id"] = company.id
        defaults["contract_type"] = "sale"
        defaults["is_membership_contract"] = True

        partner_id = defaults.get("partner_id") or self.env.context.get(
            "default_partner_id"
        )
        if partner_id:
            partner = self.env["res.partner"].browse(partner_id)
            defaults["name"] = f"{partner.display_name} - {company.display_name}"

        if company.membership_contract_yearly_defaults:
            defaults["line_recurrence"] = False
            defaults["recurring_interval"] = 1
            defaults["recurring_rule_type"] = "yearly"

        return defaults

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get("auto_invoice_membership_contract_on_save"):
            return records

        silent_import = records._use_silent_membership_import()
        for contract in records.filtered(
            lambda rec: rec.is_membership_contract and rec.generation_type == "invoice"
        ):
            # Ensure there is a first invoicing date for contracts created
            # without recurring_next_date in the form defaults.
            if not contract.recurring_next_date:
                contract.recurring_next_date = fields.Date.context_today(contract)
            if silent_import:
                invoices = contract._recurring_create_invoice()
            else:
                invoices = contract.recurring_create_invoice()
            if invoices and contract.company_id.membership_contract_yearly_defaults:
                contract.recurring_next_date = contract._membership_next_jan_first()
        return records

    @api.model
    def _invoice_followers(self, invoices):
        if self._use_silent_membership_import():
            return None
        return super()._invoice_followers(invoices)

    @api.model
    def _add_contract_origin(self, invoices):
        if self._use_silent_membership_import():
            return None
        return super()._add_contract_origin(invoices)

    def _prepare_invoice(self, date_invoice, journal=None):
        invoice_vals = super()._prepare_invoice(date_invoice, journal=journal)
        if not self.is_membership_contract:
            return invoice_vals

        commercial_partner = self.partner_id.commercial_partner_id
        invoice_partner = self.invoice_partner_id
        if (
            invoice_partner
            and invoice_partner != commercial_partner
            and invoice_partner.type == "invoice"
            and invoice_partner.commercial_partner_id == commercial_partner
        ):
            invoice_vals["delegated_member_id"] = commercial_partner.id

        return invoice_vals
