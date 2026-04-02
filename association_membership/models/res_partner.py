from collections import defaultdict

from markupsafe import Markup, escape

from odoo import _, api, fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    membership_ids = fields.One2many(
        "membership.membership",
        "partner_id",
        string="Memberships",
    )
    membership_contribution_ids = fields.One2many(
        "membership.contribution",
        "partner_id",
        string="Membership Contributions",
    )
    current_membership_number_display = fields.Char(
        string="Membership",
        compute="_compute_membership_number_displays",
    )
    all_membership_numbers_display = fields.Html(
        string="All Memberships",
        compute="_compute_membership_number_displays",
        sanitize=True,
    )

    def _all_numbered_memberships_by_partner(self):
        memberships = self.env["membership.membership"].sudo().search(
            [
                ("partner_id", "in", self.ids),
                ("state", "in", ("waiting", "active", "cancelled")),
                ("membership_number", "!=", False),
            ]
        )
        memberships_by_partner = defaultdict(list)
        for membership in memberships:
            memberships_by_partner[membership.partner_id.id].append(membership)
        return memberships_by_partner

    @api.depends(
        "membership_ids.membership_number",
        "membership_ids.state",
        "membership_ids.company_id",
        "membership_ids.company_id.partner_id",
    )
    def _compute_membership_number_displays(self):
        current_company = self.env.company
        memberships_by_partner = self._all_numbered_memberships_by_partner()
        for partner in self:
            numbered_memberships = sorted(
                memberships_by_partner.get(partner.id, []),
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
            partner.current_membership_number_display = ", ".join(current_company_numbers) or False
            partner.all_membership_numbers_display = (
                Markup("").join(all_html_items) if all_html_items else False
            )

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
