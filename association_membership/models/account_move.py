from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    def _membership_pending_welcome_memberships(self):
        self.ensure_one()
        if self.move_type != "out_invoice":
            return self.env["membership.membership"]
        return self.line_ids.mapped("membership_id").filtered(
            lambda membership: not membership.date_welcome_sent
        )

    def _membership_activation_mail_template(self):
        self.ensure_one()
        memberships = self._membership_pending_welcome_memberships()
        if not memberships:
            return False
        return memberships[:1].company_id.membership_activation_invoice_template_id

    def _get_mail_template(self):
        template = self[:1]._membership_activation_mail_template() if len(self) == 1 else False
        return template or super()._get_mail_template()

    def _mark_membership_welcome_sent(self, mail_template=False):
        self.ensure_one()
        template = self._membership_activation_mail_template()
        if not (template and mail_template == template):
            return False
        memberships = self._membership_pending_welcome_memberships()
        if memberships:
            memberships.write({"date_welcome_sent": fields.Date.context_today(self)})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        if moves.filtered(
            lambda move: move.line_ids.filtered(
                lambda line: line.membership_id or line.membership_contribution_id
            )
        ):
            moves._membership_after_accounting_update()
        return moves

    def action_post(self):
        previous_state = {move.id: move.state for move in self}
        result = super().action_post()
        self._membership_after_accounting_update(previous_state=previous_state)
        return result

    def write(self, vals):
        previous_state = {move.id: move.state for move in self}
        previous_payment_state = {move.id: move.payment_state for move in self}
        result = super().write(vals)
        if self.filtered(
            lambda move: move.line_ids.filtered(
                lambda line: line.membership_id or line.membership_contribution_id
            )
        ):
            self._membership_after_accounting_update(
                previous_state=previous_state,
                previous_payment_state=previous_payment_state,
            )
        return result

    def _membership_after_accounting_update(self, previous_state=False, previous_payment_state=False):
        contributions = self.mapped("line_ids.membership_contribution_id")
        if not contributions:
            return
        contributions._sync_accounting_links_from_lines()

        previous_state = previous_state or {}
        previous_payment_state = previous_payment_state or {}

        posted_refunds = self.filtered(
            lambda move: move.move_type == "out_refund"
            and previous_state.get(move.id) != "posted"
            and move.state == "posted"
        )
        for move in posted_refunds:
            move.line_ids.mapped("membership_contribution_id").post_refund_review_message(move)

        newly_paid_moves = self.filtered(
            lambda move: move.move_type == "out_invoice"
            and move.company_id.membership_auto_activate_on_payment
            and previous_payment_state.get(move.id) not in ("in_payment", "paid")
            and move.payment_state in ("in_payment", "paid")
        )
        for move in newly_paid_moves:
            memberships = move.line_ids.mapped("membership_contribution_id.membership_id")
            memberships.action_activate_from_payment(invoice=move)
