from odoo import models


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_post(self):
        previous_state = {move.id: move.state for move in self}
        result = super().action_post()
        self._membership_after_accounting_update(previous_state=previous_state)
        return result

    def write(self, vals):
        previous_state = {move.id: move.state for move in self}
        previous_payment_state = {move.id: move.payment_state for move in self}
        result = super().write(vals)
        if self.filtered(lambda move: move.line_ids.membership_contribution_id):
            self._membership_after_accounting_update(
                previous_state=previous_state,
                previous_payment_state=previous_payment_state,
            )
        return result

    def _compute_payment_state(self):
        # ``payment_state`` is a stored computed field: its changes never reach
        # ``write()``, so the membership hooks (tax receipts, auto-activation)
        # must run here to see invoice payments. Previous states are read
        # directly from the database because the cached values are already
        # invalidated when this compute is triggered.
        previous_state = {}
        previous_payment_state = {}
        if self.ids:
            self.flush_model()
            self.env.cr.execute(
                "SELECT id, state, payment_state FROM account_move WHERE id = ANY(%s)",
                [self.ids],
            )
            for move_id, state, payment_state in self.env.cr.fetchall():
                previous_state[move_id] = state
                previous_payment_state[move_id] = payment_state
        super()._compute_payment_state()
        self._membership_after_accounting_update(
            previous_state=previous_state,
            previous_payment_state=previous_payment_state,
        )

    def _membership_after_accounting_update(self, previous_state=False, previous_payment_state=False):
        previous_state = previous_state or {}
        previous_payment_state = previous_payment_state or {}

        for move in self.filtered(
            lambda m: m.move_type == "out_refund"
            and previous_state.get(m.id) != "posted"
            and m.state == "posted"
        ):
            move.line_ids.mapped("membership_contribution_id").post_refund_review_message(move)

        for move in self.filtered(
            lambda m: m.move_type == "out_invoice"
            and previous_payment_state.get(m.id) not in ("in_payment", "paid")
            and m.payment_state in ("in_payment", "paid")
        ):
            contributions = move.line_ids.mapped("membership_contribution_id")
            if move.company_id.membership_auto_activate_on_payment:
                contributions.mapped("membership_id").action_activate_from_payment(invoice=move)
            for contribution in contributions:
                contribution._maybe_issue_tax_receipt(move)
