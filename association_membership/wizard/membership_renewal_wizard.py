from datetime import date

from odoo import Command, _, fields, models


class MembershipRenewalWizard(models.TransientModel):
    _name = "membership.renewal.wizard"
    _description = "Membership Renewal Wizard"

    target_year = fields.Integer(
        required=True,
        default=lambda self: fields.Date.today().year + 1,
    )
    company_ids = fields.Many2many(
        "res.company",
        string="Companies",
        default=lambda self: [(6, 0, self.env.companies.ids)],
        required=True,
    )
    product_ids = fields.Many2many("product.product", string="Membership Products")
    dry_run = fields.Boolean(string="Dry Run")
    invoice_date = fields.Date()
    auto_post = fields.Boolean()
    result_line_ids = fields.One2many(
        "membership.renewal.wizard.line",
        "wizard_id",
        string="Results",
    )

    def _renewal_window(self):
        self.ensure_one()
        return date(self.target_year, 1, 1), date(self.target_year, 12, 31)

    def _candidate_memberships(self):
        self.ensure_one()
        target_start, target_end = self._renewal_window()
        domain = [
            ("state", "=", "active"),
            ("company_id", "in", self.company_ids.ids),
            ("date_start", "<=", target_end),
            "|",
            ("date_end", "=", False),
            ("date_end", ">=", target_start),
        ]
        if self.product_ids:
            domain.append(("product_id", "in", self.product_ids.ids))
        return self.env["membership.membership"].search(domain)

    def _existing_contribution_membership_ids(self, memberships):
        contribution_memberships = self.env["membership.contribution"].search(
            [
                ("membership_id", "in", memberships.ids),
                ("membership_year", "=", self.target_year),
            ]
        ).mapped("membership_id")
        return set(contribution_memberships.ids)

    def _build_result_values(self, item, status, message, invoice=False):
        return {
            "membership_id": item["membership"].id,
            "partner_id": item["membership"].partner_id.id,
            "company_id": item["membership"].company_id.id,
            "status": status,
            "message": message,
            "amount_expected": item["amount_expected"],
            "is_free": item["is_free"],
            "invoice_id": invoice.id if invoice else False,
        }

    def action_run(self):
        self.ensure_one()
        self.result_line_ids.unlink()

        result_commands = [Command.clear()]
        candidate_memberships = self._candidate_memberships()
        existing_membership_ids = self._existing_contribution_membership_ids(candidate_memberships)
        eligible_memberships = candidate_memberships.filtered(
            lambda membership: membership.id not in existing_membership_ids
        )
        paid_groups = {}

        for membership in eligible_memberships:
            is_free = membership._resolve_is_free()
            amount_expected = membership._resolve_amount_expected(is_free=is_free)
            item = {
                "membership": membership,
                "is_free": is_free,
                "amount_expected": amount_expected,
                "invoice_partner": membership._get_invoice_partner(),
                "currency": membership.currency_id,
            }
            if is_free:
                try:
                    with self.env.cr.savepoint():
                        if not self.dry_run:
                            contribution_vals = membership._prepare_contribution_create_values(
                                self.target_year,
                                is_free=True,
                                amount_expected=0.0,
                                invoice_partner_id=item["invoice_partner"].id,
                            )
                            self.env["membership.contribution"].with_context(
                                skip_membership_invoice_creation=True
                            ).create(contribution_vals)
                    result_commands.append(
                        Command.create(
                            self._build_result_values(
                                item,
                                "created",
                                _("Created free contribution."),
                            )
                        )
                    )
                except Exception as error:
                    self.env.invalidate_all()
                    result_commands.append(
                        Command.create(
                            self._build_result_values(
                                item,
                                "error",
                                str(error),
                            )
                        )
                    )
                continue
            group_key = (
                item["invoice_partner"].id,
                membership.company_id.id,
                self.target_year,
                membership.currency_id.id,
            )
            paid_groups.setdefault(group_key, []).append(item)

        skipped_memberships = candidate_memberships.filtered(
            lambda membership: membership.id in existing_membership_ids
        )
        for membership in skipped_memberships:
            result_commands.append(
                Command.create(
                    {
                        "membership_id": membership.id,
                        "partner_id": membership.partner_id.id,
                        "company_id": membership.company_id.id,
                        "status": "skipped",
                        "message": _(
                            "Skipped because a contribution already exists for %s."
                        )
                        % self.target_year,
                    }
                )
            )

        for group_items in paid_groups.values():
            try:
                invoice = False
                with self.env.cr.savepoint():
                    if not self.dry_run:
                        contributions = self.env["membership.contribution"].with_context(
                            skip_membership_invoice_creation=True
                        ).create(
                            [
                                item["membership"]._prepare_contribution_create_values(
                                    self.target_year,
                                    amount_expected=item["amount_expected"],
                                    invoice_partner_id=item["invoice_partner"].id,
                                )
                                for item in group_items
                            ]
                        )
                        invoice = contributions._create_membership_invoices(
                            auto_post=self.auto_post,
                            invoice_date=self.invoice_date,
                        )[:1]
                for item in group_items:
                    result_commands.append(
                        Command.create(
                            self._build_result_values(
                                item,
                                "created",
                                _("Created contribution and invoice draft."),
                                invoice=invoice,
                            )
                        )
                    )
            except Exception as error:
                self.env.invalidate_all()
                for item in group_items:
                    result_commands.append(
                        Command.create(
                            self._build_result_values(
                                item,
                                "error",
                                str(error),
                            )
                        )
                    )

        self.write({"result_line_ids": result_commands})
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }


class MembershipRenewalWizardLine(models.TransientModel):
    _name = "membership.renewal.wizard.line"
    _description = "Membership Renewal Wizard Result"

    wizard_id = fields.Many2one(
        "membership.renewal.wizard",
        required=True,
        ondelete="cascade",
    )
    membership_id = fields.Many2one("membership.membership", readonly=True)
    partner_id = fields.Many2one("res.partner", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    status = fields.Selection(
        [
            ("created", "Created"),
            ("skipped", "Skipped"),
            ("error", "Error"),
        ],
        required=True,
        readonly=True,
    )
    message = fields.Char(readonly=True)
    amount_expected = fields.Monetary(readonly=True)
    currency_id = fields.Many2one(
        "res.currency",
        related="membership_id.currency_id",
        readonly=True,
    )
    is_free = fields.Boolean(readonly=True)
    invoice_id = fields.Many2one("account.move", readonly=True)
