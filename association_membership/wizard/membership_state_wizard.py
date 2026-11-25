from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

# The states a membership may be in for each bulk change (D83). Terminated ones
# go back to draft first; the single wizards are the reference.
SOURCE_STATES = {
    "active": ("draft", "waiting", "cancelled"),
    "cancelled": ("active",),
    "draft": ("waiting", "cancelled", "terminated"),
}


class MembershipStateWizard(models.TransientModel):
    """Change the state of several memberships at once (D83).

    Each membership goes through the single Activate or Cancel wizard with
    its own company's strategy and templates, so a bulk change does exactly
    what one at a time would. Memberships whose state does not allow the
    change are skipped, and one that fails does not stop the others.
    """

    _name = "membership.state.wizard"
    _description = "Change Membership State"

    membership_ids = fields.Many2many(
        "membership.membership",
        string="Memberships",
        default=lambda self: self._default_membership_ids(),
    )
    target_state = fields.Selection(
        [
            ("active", "Activate"),
            ("cancelled", "Cancel"),
            ("draft", "Revert to Draft"),
        ],
        string="Change",
        required=True,
        default="active",
    )
    summary = fields.Char(compute="_compute_summary")

    # Activate
    send_welcome_message = fields.Boolean(
        string="Send Welcome Email",
        help="Each membership's welcome template of its association, unchanged, to its"
        " usual recipients. Only on a first activation, as in the single wizard.",
    )

    # Cancel: the same for all.
    date_cancelled = fields.Date(default=lambda self: fields.Date.context_today(self))
    date_end = fields.Date(
        default=lambda self: date(fields.Date.context_today(self).year, 12, 31),
    )
    cancel_reason = fields.Text()
    open_period_handling = fields.Selection(
        [
            ("keep", "Keep them"),
            ("drop", "Cancel draft invoices and delete the periods"),
        ],
        string="Unpaid periods",
        default="keep",
        required=True,
        help="Periods of the cancellation year and later that are not paid."
        " Posted invoices are never touched: reverse them with a credit note.",
    )
    send_cancellation_message = fields.Boolean(
        string="Send Cancellation Email",
        help="Each membership's cancellation template of its association, unchanged,"
        " to its usual recipients.",
    )

    def _default_membership_ids(self):
        if self.env.context.get("active_model") != "membership.membership":
            return self.env["membership.membership"]
        return self.env["membership.membership"].browse(self.env.context.get("active_ids") or [])

    def _eligible(self):
        self.ensure_one()
        return self.membership_ids.filtered(
            lambda membership: membership.state in SOURCE_STATES[self.target_state]
        )

    @api.depends("membership_ids", "target_state")
    def _compute_summary(self):
        for wizard in self:
            if not wizard.target_state:
                wizard.summary = False
                continue
            eligible = wizard._eligible()
            skipped = wizard.membership_ids - eligible
            summary = _("%(count)s of %(total)s memberships will change.") % {
                "count": len(eligible),
                "total": len(wizard.membership_ids),
            }
            if skipped:
                states = dict(skipped._fields["state"]._description_selection(self.env))
                by_state = ", ".join(
                    "%s %s" % (len(skipped.filtered(lambda m, s=state: m.state == s)), states[state])
                    for state in sorted(set(skipped.mapped("state")))
                )
                summary += " " + _("Skipped, as their state does not allow it: %s.") % by_state
            wizard.summary = summary

    def _activate(self, membership):
        wizard = (
            self.env["membership.activate.wizard"]
            .with_context(default_membership_id=membership.id)
            .create({})
        )
        if not self.send_welcome_message:
            wizard.send_welcome_message = False
        wizard.action_confirm()

    def _cancel(self, membership):
        wizard = (
            self.env["membership.cancel.wizard"]
            .with_context(
                default_membership_id=membership.id,
                # The unpaid periods are found from the cancellation date.
                default_date_cancelled=self.date_cancelled,
            )
            .create(
                {
                    "date_cancelled": self.date_cancelled,
                    "date_end": self.date_end,
                    "cancel_reason": self.cancel_reason,
                    "open_period_handling": self.open_period_handling,
                }
            )
        )
        wizard.send_cancellation_message = bool(
            self.send_cancellation_message and wizard.cancellation_template_id
        )
        wizard.action_confirm()

    def action_confirm(self):
        self.ensure_one()
        if self.target_state == "cancelled" and not (self.date_cancelled and self.date_end):
            raise UserError(_("A cancellation needs a cancellation date and an end date."))
        change = {
            "active": self._activate,
            "cancelled": self._cancel,
            "draft": lambda membership: membership.action_revert_to_draft(),
        }[self.target_state]
        eligible = self._eligible()
        failed = []
        for membership in eligible:
            try:
                with self.env.cr.savepoint():
                    change(membership)
            except (UserError, ValueError) as error:
                failed.append("%s: %s" % (membership.display_name, error))
        done = len(eligible) - len(failed)
        message = _("%(done)s of %(total)s memberships changed.") % {
            "done": done,
            "total": len(self.membership_ids),
        }
        if failed:
            message += "\n" + _("Not changed:") + "\n" + "\n".join(failed)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Change State"),
                "message": message,
                "type": "warning" if failed else "success",
                "sticky": bool(failed),
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
