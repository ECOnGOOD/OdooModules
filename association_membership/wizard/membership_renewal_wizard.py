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
        today = fields.Date.context_today(self)
        domain = [
            # `cancelled` stays in: a member who cancels in March effective
            # 31 Dec still owes that year. The date_end filter below excludes
            # them from later years.
            ("state", "in", ("active", "cancelled")),
            ("company_id", "in", self.company_ids.ids),
            ("date_start", "<=", target_end),
            "|",
            ("date_end", "=", False),
            "&",
            ("date_end", ">=", target_start),
            # Already expired: only the termination cron has not caught up yet.
            ("date_end", ">=", today),
        ]
        if self.product_ids:
            domain.append(("product_id", "in", self.product_ids.ids))
        return self.env["membership.membership"].search(domain)

    def _existing_period_membership_ids(self, memberships):
        period_memberships = self.env["membership.period"].search(
            [
                ("membership_id", "in", memberships.ids),
                ("membership_year", "=", self.target_year),
            ]
        ).mapped("membership_id")
        return set(period_memberships.ids)

    def _result_message(self, strategy, is_free=False):
        if self.dry_run:
            return _("Would create a period.")
        if is_free or strategy == "manual":
            return _("Created period.")
        if strategy == "confirm":
            return _("Created period and confirmed invoice.")
        # `draft` and anything unexpected leave the invoice in draft.
        return _("Created period and draft invoice.")

    def _build_result_values(self, item, status, message, invoice=False):
        return {
            "membership_id": item["membership"].id,
            "partner_id": item["membership"].partner_id.id,
            "company_id": item["membership"].company_id.id,
            "status": status,
            "message": message,
            "amount": item["amount"],
            "invoice_id": invoice.id if invoice else False,
        }

    def _skipped_result(self, membership, message):
        return Command.create(
            {
                "membership_id": membership.id,
                "partner_id": membership.partner_id.id,
                "company_id": membership.company_id.id,
                "status": "skipped",
                "message": message,
            }
        )

    def action_run(self):
        self.ensure_one()
        self.result_line_ids.unlink()

        result_commands = [Command.clear()]
        candidate_memberships = self._candidate_memberships()
        existing_membership_ids = self._existing_period_membership_ids(candidate_memberships)
        # Retiring a tier means archiving its variant; those members need a new tier first.
        archived_tier_memberships = candidate_memberships.filtered(
            lambda membership: membership.id not in existing_membership_ids
            and not membership.product_id.active
        )
        eligible_memberships = candidate_memberships.filtered(
            lambda membership: membership.id not in existing_membership_ids
        ) - archived_tier_memberships
        grouped_items = {}

        for membership in eligible_memberships:
            amount = membership.amount or 0.0
            is_free = float(amount or 0.0) == 0.0
            item = {
                "membership": membership,
                "amount": amount,
                "invoice_partner": membership._get_invoice_partner(),
                "is_free": is_free,
                "strategy": membership._get_invoicing_strategy(),
            }
            if is_free:
                try:
                    with self.env.cr.savepoint():
                        if not self.dry_run:
                            membership.env["membership.period"].create(
                                membership._prepare_period_create_values(
                                    self.target_year,
                                    amount=0.0,
                                    invoice_partner_id=item["invoice_partner"].id,
                                )
                            )
                    result_commands.append(
                        Command.create(
                            self._build_result_values(
                                item,
                                "created",
                                self._result_message(item["strategy"], is_free=True),
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
            grouped_items.setdefault(group_key, []).append(item)

        skipped_memberships = candidate_memberships.filtered(
            lambda membership: membership.id in existing_membership_ids
        )
        for membership in skipped_memberships:
            result_commands.append(
                self._skipped_result(
                    membership,
                    _("Skipped because a period already exists for %s.") % self.target_year,
                )
            )
        for membership in archived_tier_memberships:
            result_commands.append(
                self._skipped_result(
                    membership,
                    _(
                        "Skipped because the membership product %s is archived."
                        " Choose a current tier on the membership first."
                    )
                    % membership.product_id.display_name,
                )
            )

        for group in grouped_items.values():
            try:
                invoice = False
                with self.env.cr.savepoint():
                    if not self.dry_run:
                        periods = self.env["membership.period"].create(
                            [
                                item["membership"]._prepare_period_create_values(
                                    self.target_year,
                                    amount=item["amount"],
                                    invoice_partner_id=item["invoice_partner"].id,
                                )
                                for item in group
                            ]
                        )
                        invoice = periods._apply_invoicing_strategy(
                            invoice_date=self.invoice_date,
                        )[:1]
                for item in group:
                    result_commands.append(
                        Command.create(
                            self._build_result_values(
                                item,
                                "created",
                                self._result_message(item["strategy"]),
                                invoice=invoice,
                            )
                        )
                    )
            except Exception as error:
                self.env.invalidate_all()
                for item in group:
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
    amount = fields.Monetary(readonly=True)
    currency_id = fields.Many2one(
        "res.currency",
        related="membership_id.currency_id",
        readonly=True,
    )
    invoice_id = fields.Many2one("account.move", readonly=True)
