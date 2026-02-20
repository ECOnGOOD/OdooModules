# models/res_partner.py
from markupsafe import Markup, escape

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

class ResPartner(models.Model):
    _inherit = "res.partner"

    # Legacy technical name kept for backward compatibility.
    association_member_number = fields.Char(
        string="Member Number",
        company_dependent=True,
        copy=False,
        help="Unique identifier for the member in this company.",
    )

    member_number = fields.Char(
        related="association_member_number",
        string="Member Number",
        readonly=False,
    )

    all_membership_numbers_display = fields.Char(
        string="All Membership Numbers",
        compute="_compute_all_membership_numbers",
        help="Legacy plain-text display of member numbers across companies.",
    )

    all_member_numbers_display = fields.Html(
        string="Member Numbers",
        compute="_compute_all_membership_numbers",
        sanitize=True,
        help="Displays member numbers across all companies the user can access.",
    )

    @api.depends("association_member_number")
    def _compute_all_membership_numbers(self):
        companies = self.env.user.company_ids.sorted(key=lambda c: c.name)
        current_company = self.env.company
        for partner in self:
            display_lines = []
            html_items = []
            sorted_companies = sorted(
                companies,
                key=lambda company: (company != current_company, company.name),
            )
            for company in sorted_companies:
                number = partner.with_company(company).association_member_number
                if not number:
                    continue
                display_lines.append(f"{company.name}: {number}")
                html_items.append(
                    Markup("<li><strong>%s</strong>: <code>%s</code></li>")
                    % (escape(company.name), escape(number))
                )
            partner.all_membership_numbers_display = "\n".join(display_lines)
            partner.all_member_numbers_display = (
                Markup("<ul>%s</ul>") % Markup("").join(html_items)
                if html_items
                else False
            )

    def _find_member_number_conflict(self, member_number):
        self.ensure_one()
        all_companies = self.env["res.company"].sudo().search([])
        partner_model = self.env["res.partner"].sudo().with_context(active_test=False)
        current_company = self.env.company

        for company in all_companies:
            company_number = self.with_company(company).association_member_number
            if (
                company_number
                and company_number == member_number
                and company != current_company
            ):
                return self, company

        for company in all_companies:
            conflict = partner_model.with_company(company).search(
                [
                    ("association_member_number", "=", member_number),
                    ("id", "!=", self.id),
                ],
                limit=1,
            )
            if conflict:
                return conflict, company
        return self.env["res.partner"], self.env["res.company"]

    @api.constrains("association_member_number")
    def _check_unique_member_number(self):
        for partner in self:
            if not partner.association_member_number:
                continue
            conflict_partner, conflict_company = partner._find_member_number_conflict(
                partner.association_member_number
            )
            if not conflict_partner:
                continue
            if conflict_partner.id == partner.id:
                raise ValidationError(
                    _(
                        "This contact already has Member Number '%(number)s' in "
                        "company '%(company)s'. Member Numbers must be globally unique."
                    )
                    % {
                        "number": partner.association_member_number,
                        "company": conflict_company.display_name,
                    }
                )
            raise ValidationError(
                _(
                    "Member Number '%(number)s' is already assigned to '%(partner)s' "
                    "in company '%(company)s'. Member Numbers must be globally unique."
                )
                % {
                    "number": partner.association_member_number,
                    "partner": conflict_partner.display_name,
                    "company": conflict_company.display_name,
                }
            )
