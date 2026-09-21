from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    membership_id = fields.Many2one("membership.membership", copy=False)
    membership_period_id = fields.Many2one("membership.period", copy=False)
    membership_year = fields.Integer(copy=False)

    @api.onchange("membership_period_id")
    def _onchange_membership_period_id(self):
        if self.membership_period_id:
            self.membership_id = self.membership_period_id.membership_id
            self.membership_year = self.membership_period_id.membership_year

    @api.constrains("membership_id", "membership_period_id", "membership_year")
    def _check_membership_metadata(self):
        for line in self:
            period = line.membership_period_id
            if not period:
                continue
            if line.membership_id and line.membership_id != period.membership_id:
                raise ValidationError(
                    self.env._("The membership line and period must point to the same membership.")
                )
            if line.membership_year and line.membership_year != period.membership_year:
                raise ValidationError(
                    self.env._("The membership year must match the linked period year.")
                )

    @api.model
    def _prepare_membership_metadata_values(self, vals):
        period_id = vals.get("membership_period_id")
        if period_id:
            period = self.env["membership.period"].browse(period_id)
            vals.setdefault("membership_id", period.membership_id.id)
            vals.setdefault("membership_year", period.membership_year)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals = [self._prepare_membership_metadata_values(vals) for vals in vals_list]
        lines = super().create(prepared_vals)
        periods = lines.mapped("membership_period_id")
        if periods:
            periods._sync_accounting_links_from_lines()
        return lines

    def write(self, vals):
        previous_periods = self.mapped("membership_period_id")
        vals = self._prepare_membership_metadata_values(vals)
        result = super().write(vals)
        if {"membership_period_id", "move_id", "membership_id", "membership_year"} & set(vals):
            (previous_periods | self.mapped("membership_period_id"))._sync_accounting_links_from_lines()
        return result

    def unlink(self):
        periods = self.mapped("membership_period_id")
        result = super().unlink()
        if periods:
            periods._sync_accounting_links_from_lines()
        return result
