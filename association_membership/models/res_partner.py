from collections import defaultdict

from odoo import _, api, fields, models


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

    def _compute_membership_snapshot(self):
        company_ids = self.env.companies.ids or self.env.user.company_ids.ids
        memberships = self.env["membership.membership"].search(
            [
                ("partner_id", "in", self.ids),
                ("company_id", "in", company_ids),
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
        contributions_by_partner = defaultdict(lambda: self.env["membership.contribution"])
        for membership in memberships:
            memberships_by_partner[membership.partner_id.id] |= membership
        for contribution in contributions:
            contributions_by_partner[contribution.partner_id.id] |= contribution

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
                "default_invoice_partner_id": self.id,
                "default_company_id": self.env.company.id,
            },
        }
