from datetime import date

from odoo import _, fields, models


class MembershipCancelWizard(models.TransientModel):
    _name = "membership.cancel.wizard"
    _description = "Membership Cancel Wizard"

    membership_id = fields.Many2one(
        "membership.membership",
        required=True,
        readonly=True,
    )
    date_cancelled = fields.Date(
        required=True,
        default=lambda self: fields.Date.context_today(self),
    )
    date_end = fields.Date(
        required=True,
        default=lambda self: date(fields.Date.today().year, 12, 31),
    )
    cancel_reason = fields.Text()

    def action_confirm(self):
        self.ensure_one()
        self.membership_id._do_transition(
            "cancelled",
            date_cancelled=self.date_cancelled,
            date_end=self.date_end,
            cancel_reason=self.cancel_reason,
        )
        return {"type": "ir.actions.act_window_close"}
