from datetime import date

from markupsafe import Markup, escape

from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    membership_timeline_html = fields.Html(
        string="Membership Timeline",
        compute="_compute_membership_contract_glue_summaries",
        sanitize=True,
        help="Chronological timeline derived from memberships, membership contracts, and invoices.",
    )
    relationship_summary_html = fields.Html(
        string="Relationships",
        compute="_compute_membership_contract_glue_summaries",
        sanitize=True,
        help="Current and past partner relationships visible from the contact record.",
    )

    def _timeline_date_key(self, value):
        if not value:
            return date.min
        return fields.Date.to_date(value)

    def _build_html_list(self, title, items):
        if not items:
            return False
        body = Markup("").join(items)
        return Markup("<div><strong>%s</strong><ul>%s</ul></div>") % (
            escape(title),
            body,
        )

    def _membership_invoices(self):
        self.ensure_one()
        commercial_partner = self.commercial_partner_id or self
        return self.env["account.move"].search(
            [
                ("move_type", "in", ["out_invoice", "out_refund"]),
                "|",
                ("partner_id", "=", commercial_partner.id),
                ("delegated_member_id", "=", commercial_partner.id),
            ],
            order="invoice_date desc, create_date desc, id desc",
        )

    def _compute_membership_contract_glue_summaries(self):
        today = fields.Date.context_today(self)
        for partner in self:
            timeline_entries = []

            for line in sorted(
                partner.member_lines,
                key=lambda item: (
                    partner._timeline_date_key(item.date_from or item.date or item.date_to),
                    item.id,
                ),
                reverse=True,
            ):
                summary = _html_line(
                    title="%s (%s)" % (
                        line.membership_id.display_name or "Membership",
                        line.state or "unknown",
                    ),
                    details=[
                        "Period: %s to %s" % (line.date_from or "-", line.date_to or "-"),
                        "Amount: %s" % (line.member_price if line.member_price is not None else "-"),
                        "Company: %s" % (line.company_id.display_name or "No company"),
                        "Invoice: %s"
                        % (
                            line.account_invoice_id.display_name
                            or line.account_invoice_id.name
                            or "No invoice"
                        ),
                    ],
                )
                timeline_entries.append(
                    (
                        partner._timeline_date_key(line.date_from or line.date or line.date_to),
                        summary,
                    )
                )

            for contract in sorted(
                partner.contract_ids.filtered("is_membership_contract"),
                key=lambda item: (
                    partner._timeline_date_key(item.date_start or item.recurring_next_date),
                    item.id,
                ),
                reverse=True,
            ):
                summary = _html_line(
                    title="Membership contract: %s" % (contract.display_name or contract.name),
                    details=[
                        "Period: %s to %s" % (contract.date_start or "-", contract.date_end or "-"),
                        "Next invoice: %s" % (contract.recurring_next_date or "-"),
                        "Invoices: %s" % contract.invoice_count,
                        "Company: %s" % (contract.company_id.display_name or "No company"),
                    ],
                )
                timeline_entries.append(
                    (
                        partner._timeline_date_key(contract.date_start or contract.recurring_next_date),
                        summary,
                    )
                )

            for invoice in partner._membership_invoices():
                summary = _html_line(
                    title="Invoice: %s" % (invoice.display_name or invoice.name or "Draft"),
                    details=[
                        "Type: %s" % invoice.move_type,
                        "State: %s / %s" % (invoice.state, invoice.payment_state),
                        "Invoice date: %s" % (invoice.invoice_date or "-"),
                        "Amount: %s" % invoice.amount_total,
                        "Delegated member: %s"
                        % (
                            invoice.delegated_member_id.display_name
                            if invoice.delegated_member_id
                            else "-"
                        ),
                    ],
                )
                timeline_entries.append(
                    (
                        partner._timeline_date_key(invoice.invoice_date or invoice.create_date),
                        summary,
                    )
                )

            timeline_entries.sort(key=lambda item: item[0], reverse=True)
            partner.membership_timeline_html = partner._build_html_list(
                "Membership Timeline",
                [item[1] for item in timeline_entries],
            )

            if "relation_all_ids" not in partner._fields:
                partner.relationship_summary_html = False
                continue

            current_items = []
            past_items = []
            for relation in sorted(
                partner.with_context(active_test=False).relation_all_ids,
                key=lambda item: (
                    partner._timeline_date_key(item.date_start or item.date_end),
                    item.id,
                ),
                reverse=True,
            ):
                relation_item = _html_line(
                    title="%s: %s"
                    % (
                        relation.type_selection_id.display_name
                        or relation.type_id.display_name
                        or "Relation",
                        relation.other_partner_id.display_name or "Unknown",
                    ),
                    details=[
                        "Active: %s" % ("Yes" if relation.active else "No"),
                        "Period: %s to %s" % (relation.date_start or "-", relation.date_end or "-"),
                    ],
                )
                is_past = bool(relation.date_end and relation.date_end < today)
                if is_past:
                    past_items.append(relation_item)
                else:
                    current_items.append(relation_item)

            sections = []
            current_section = partner._build_html_list("Current Relationships", current_items)
            if current_section:
                sections.append(current_section)
            past_section = partner._build_html_list("Past Relationships", past_items)
            if past_section:
                sections.append(past_section)
            partner.relationship_summary_html = (
                Markup("").join(sections) if sections else False
            )


def _html_line(*, title, details):
    detail_items = []
    for detail in details:
        if detail:
            detail_items.append(Markup("<li>%s</li>") % escape(str(detail)))
    details_html = (
        Markup("<ul>%s</ul>") % Markup("").join(detail_items) if detail_items else Markup("")
    )
    return Markup("<li><strong>%s</strong>%s</li>") % (escape(str(title)), details_html)
