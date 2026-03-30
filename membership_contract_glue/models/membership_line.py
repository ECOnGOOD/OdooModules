from odoo import _, api, models
from odoo.exceptions import ValidationError


class MembershipLine(models.Model):
    _inherit = "membership.membership_line"

    def _membership_logging_disabled(self):
        context = self.env.context
        return bool(
            context.get("tracking_disable")
            or context.get("silent_membership_contract_import")
        )

    def _validate_required_dates(self):
        for line in self:
            if not line.date_from or not line.date_to:
                raise ValidationError(
                    _("Membership lines require both a start date and an end date.")
                )

    def _state_label(self, value):
        selection = self._fields["state"].selection
        if callable(selection):
            selection = selection(self.env)
        return dict(selection).get(value, value or _("Unset"))

    def _log_membership_state_change(self, old_state):
        self.ensure_one()
        if old_state == self.state or self._membership_logging_disabled():
            return

        partner = self.partner.commercial_partner_id or self.partner
        if not partner:
            return

        body = _(
            "Membership status changed for <strong>%(membership)s</strong>: "
            "%(old_state)s to %(new_state)s.<br/>"
            "Period: %(date_from)s to %(date_to)s<br/>"
            "Company: %(company)s<br/>"
            "Invoice: %(invoice)s"
        ) % {
            "membership": self.membership_id.display_name or _("Membership"),
            "old_state": self._state_label(old_state),
            "new_state": self._state_label(self.state),
            "date_from": self.date_from or "-",
            "date_to": self.date_to or "-",
            "company": self.company_id.display_name or _("No company"),
            "invoice": self.account_invoice_id.display_name or _("No invoice"),
        }
        message_context = {
            "mail_create_nosubscribe": True,
            "mail_auto_subscribe_no_notify": True,
            "mail_notify_noemail": True,
        }
        partner.with_context(**message_context).message_post(
            body=body,
            subtype_xmlid="mail.mt_note",
        )

        contracts = partner.contract_ids.filtered(
            lambda contract: contract.is_membership_contract
            and (
                not self.company_id
                or not contract.company_id
                or contract.company_id == self.company_id
            )
        )
        if len(contracts) == 1:
            contracts.with_context(**message_context).message_post(
                body=body,
                subtype_xmlid="mail.mt_note",
            )

    @staticmethod
    def _date_fields_in_vals(vals):
        return any(
            field_name in vals for field_name in ("date", "date_from", "date_to", "membership_id")
        )

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._validate_required_dates()
        return records

    def write(self, vals):
        old_states = {}
        if "state" in vals and not self._membership_logging_disabled():
            old_states = {line.id: line.state for line in self}

        result = super().write(vals)

        if self._date_fields_in_vals(vals):
            self._validate_required_dates()

        for line in self:
            if line.id in old_states:
                line._log_membership_state_change(old_states[line.id])
        return result
