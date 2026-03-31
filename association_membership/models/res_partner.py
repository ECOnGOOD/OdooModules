from collections import defaultdict

from markupsafe import Markup, escape

from odoo import _, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    membership_ids = fields.One2many(
        "membership.membership",
        "partner_id",
        string="Memberships",
    )
    current_membership_ids = fields.Many2many(
        "membership.membership",
        compute="_compute_membership_snapshot",
        string="Current Memberships",
    )
    ended_membership_ids = fields.Many2many(
        "membership.membership",
        compute="_compute_membership_snapshot",
        string="Ended Memberships",
    )
    membership_contribution_ids = fields.Many2many(
        "membership.contribution",
        compute="_compute_membership_snapshot",
        string="Membership Contributions",
    )
    current_membership_count = fields.Integer(compute="_compute_membership_snapshot")
    ended_membership_count = fields.Integer(compute="_compute_membership_snapshot")
    membership_company_names = fields.Char(compute="_compute_membership_snapshot")
    membership_latest_contribution_year = fields.Integer(compute="_compute_membership_snapshot")
    membership_latest_billing_status = fields.Char(compute="_compute_membership_snapshot")
    current_membership_number_display = fields.Char(
        string="Membership",
        compute="_compute_membership_snapshot",
    )
    all_membership_numbers_display = fields.Html(
        string="All Memberships",
        compute="_compute_membership_snapshot",
        sanitize=True,
    )
    current_member_numbers_kanban = fields.Text(
        string="Membership Numbers (Kanban)",
        compute="_compute_membership_snapshot",
    )

    def _compute_membership_snapshot(self):
        company_ids = self.env.user.company_ids.ids
        memberships = self.env["membership.membership"].search(
            [
                ("partner_id", "in", self.ids),
                ("company_id", "in", company_ids),
            ]
        )
        all_numbered_memberships = self.env["membership.membership"].sudo().search(
            [
                ("partner_id", "in", self.ids),
                ("state", "in", ("waiting", "active")),
                ("membership_number", "!=", False),
            ]
        )
        contributions = self.env["membership.contribution"].search(
            [
                ("partner_id", "in", self.ids),
                ("company_id", "in", company_ids),
            ],
            order="membership_year desc, id desc",
        )
        memberships_by_partner = defaultdict(lambda: self.env["membership.membership"])
        all_numbered_memberships_by_partner = defaultdict(list)
        contributions_by_partner = defaultdict(lambda: self.env["membership.contribution"])
        for membership in memberships:
            memberships_by_partner[membership.partner_id.id] |= membership
        for membership in all_numbered_memberships:
            all_numbered_memberships_by_partner[membership.partner_id.id].append(membership)
        for contribution in contributions:
            contributions_by_partner[contribution.partner_id.id] |= contribution

        current_company = self.env.company
        for partner in self:
            partner_memberships = memberships_by_partner.get(
                partner.id, self.env["membership.membership"]
            )
            partner_contributions = contributions_by_partner.get(
                partner.id, self.env["membership.contribution"]
            )
            current_memberships = partner_memberships.filtered(
                lambda membership: membership.state in ("waiting", "active")
            )
            ended_memberships = partner_memberships.filtered(
                lambda membership: membership.state == "cancelled"
            )
            latest_contribution = partner_contributions[:1]
            numbered_memberships = sorted(
                all_numbered_memberships_by_partner.get(partner.id, []),
                key=lambda membership: (
                    membership.company_id != current_company,
                    membership.company_id.display_name or "",
                    membership.date_start or fields.Date.today(),
                    membership.id,
                ),
            )
            current_company_numbers = [
                membership.membership_number
                for membership in numbered_memberships
                if membership.company_id == current_company
            ]
            all_html_items = []
            kanban_lines = []
            for membership in numbered_memberships:
                company_name = membership.company_id.display_name or ""
                company_partner = membership.company_id.partner_id
                if company_partner:
                    company_link = Markup('<a href="%s">%s</a>') % (
                        escape(
                            "/web#id=%s&model=res.partner&view_type=form"
                            % company_partner.id
                        ),
                        escape(company_name),
                    )
                else:
                    company_link = escape(company_name)
                all_html_items.append(
                    Markup("<div>%s (%s)</div>")
                    % (
                        escape(membership.membership_number),
                        company_link,
                    )
                )
                kanban_lines.append(membership.membership_number)

            partner.current_membership_ids = current_memberships
            partner.ended_membership_ids = ended_memberships
            partner.membership_contribution_ids = partner_contributions
            partner.current_membership_count = len(current_memberships)
            partner.ended_membership_count = len(ended_memberships)
            partner.membership_company_names = ", ".join(
                sorted(set(partner_memberships.mapped("company_id.display_name")))
            )
            partner.membership_latest_contribution_year = (
                latest_contribution.membership_year if latest_contribution else 0
            )
            partner.membership_latest_billing_status = (
                latest_contribution.billing_status if latest_contribution else False
            )
            partner.current_membership_number_display = ", ".join(current_company_numbers) or False
            partner.all_membership_numbers_display = (
                Markup("").join(all_html_items) if all_html_items else False
            )
            partner.current_member_numbers_kanban = "\n".join(kanban_lines) or False

    def action_create_membership(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Membership"),
            "res_model": "membership.membership",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_partner_id": self.id,
                "default_invoice_partner_id": self.env["membership.membership"]._resolve_default_invoice_partner(self).id,
                "default_company_id": self.env.company.id,
            },
        }
