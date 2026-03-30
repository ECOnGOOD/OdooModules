from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    membership_id = fields.Many2one("membership.membership", copy=False)
    membership_contribution_id = fields.Many2one("membership.contribution", copy=False)
    membership_year = fields.Integer(copy=False)

    @api.onchange("membership_contribution_id")
    def _onchange_membership_contribution_id(self):
        if self.membership_contribution_id:
            self.membership_id = self.membership_contribution_id.membership_id
            self.membership_year = self.membership_contribution_id.membership_year

    @api.constrains("membership_id", "membership_contribution_id", "membership_year")
    def _check_membership_metadata(self):
        for line in self:
            contribution = line.membership_contribution_id
            if not contribution:
                continue
            if line.membership_id and line.membership_id != contribution.membership_id:
                raise ValidationError(
                    self.env._("The membership line and contribution must point to the same membership.")
                )
            if line.membership_year and line.membership_year != contribution.membership_year:
                raise ValidationError(
                    self.env._("The membership year must match the linked contribution year.")
                )

    @api.model
    def _prepare_membership_metadata_values(self, vals):
        contribution_id = vals.get("membership_contribution_id")
        if contribution_id:
            contribution = self.env["membership.contribution"].browse(contribution_id)
            vals.setdefault("membership_id", contribution.membership_id.id)
            vals.setdefault("membership_year", contribution.membership_year)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals = [self._prepare_membership_metadata_values(vals) for vals in vals_list]
        lines = super().create(prepared_vals)
        lines._sync_membership_contributions()
        return lines

    def write(self, vals):
        previous_contributions = self.mapped("membership_contribution_id")
        vals = self._prepare_membership_metadata_values(vals)
        result = super().write(vals)
        if {"membership_contribution_id", "move_id", "membership_id", "membership_year"} & set(vals):
            (previous_contributions | self.mapped("membership_contribution_id"))._sync_accounting_links_from_lines()
        return result

    def unlink(self):
        contributions = self.mapped("membership_contribution_id")
        result = super().unlink()
        contributions._sync_accounting_links_from_lines()
        return result

    def _sync_membership_contributions(self):
        contributions = self.mapped("membership_contribution_id")
        for line in self.filtered("membership_contribution_id"):
            values = {}
            if line.move_id.move_type == "out_invoice":
                values = {
                    "invoice_id": line.move_id.id,
                    "invoice_line_id": line.id,
                }
            elif line.move_id.move_type == "out_refund":
                values = {"refund_move_id": line.move_id.id}
            if values:
                line.membership_contribution_id.write(values)
        contributions._sync_accounting_links_from_lines()
