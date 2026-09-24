from collections import defaultdict
from datetime import date

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .res_company import INVOICING_STRATEGY_SELECTION, normalize_year_value


PERIOD_BILLING_STATUS = [
    ("to_invoice", "To Invoice"),
    ("invoiced", "Invoiced"),
    ("partial", "Partially Paid"),
    ("paid", "Paid"),
    ("cancelled", "Cancelled"),
    ("refunded", "Refunded"),
    ("waived", "Waived"),
]


class MembershipPeriod(models.Model):
    _name = "membership.period"
    _description = "Membership Period"
    _order = "membership_year desc, id desc"
    _check_company_auto = True

    membership_id = fields.Many2one(
        "membership.membership",
        required=True,
        ondelete="restrict",
        index=True,
    )
    membership_year = fields.Integer(
        required=True,
        index=True,
        default=lambda self: self._default_membership_year(),
    )
    date_start = fields.Date(
        compute="_compute_period_dates",
        store=True,
        help="First day of the period: 1 January, or the membership start date"
             " in the year the member joined.",
    )
    date_end = fields.Date(
        compute="_compute_period_dates",
        store=True,
        help="Last day of the period: 31 December, or the membership end date"
             " in the year the membership ends.",
    )
    amount = fields.Monetary(
        default=0.0,
        copy=False,
    )
    is_free = fields.Boolean(compute="_compute_is_free", store=True)
    invoice_id = fields.Many2one("account.move", copy=False)
    refund_move_id = fields.Many2one("account.move", copy=False)
    invoice_line_id = fields.Many2one("account.move.line", copy=False)
    tax_receipt_id = fields.Many2one(
        "donation.tax.receipt",
        string="Tax Receipt",
        copy=False,
        readonly=True,
    )
    amount_invoiced = fields.Monetary(compute="_compute_amount_invoiced", store=True, readonly=False)
    amount_paid = fields.Monetary(compute="_compute_amount_paid", store=True, readonly=False)
    date_paid = fields.Date(
        string="Payment Date",
        copy=False,
        help="Date the money was received: set by \"Mark as Paid\" (manual mode)"
        " or when the invoice became paid. Tax receipts use it, and only include"
        " periods with a payment date.",
    )
    billing_status = fields.Selection(
        selection=PERIOD_BILLING_STATUS,
        compute="_compute_billing_status",
        store=True,
        readonly=False,
    )
    company_id = fields.Many2one(
        "res.company",
        related="membership_id.company_id",
        store=True,
        readonly=True,
    )
    # Written when the period is created and refreshed while it is still
    # unbilled (see _refresh_unbilled_invoicing_strategy). Once an invoice or a
    # payment exists this is history and must never be re-derived.
    membership_invoicing_strategy = fields.Selection(
        selection=INVOICING_STRATEGY_SELECTION,
        string="Applied Invoicing Strategy",
        readonly=True,
        copy=False,
    )
    partner_id = fields.Many2one(
        "res.partner",
        related="membership_id.partner_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="membership_id.currency_id",
        store=True,
        readonly=True,
    )
    note = fields.Text()
    # Own value, so a later tier change on the membership keeps past periods intact.
    product_id = fields.Many2one(
        "product.product",
        required=True,
        index=True,
        readonly=True,
    )
    invoice_partner_id = fields.Many2one("res.partner", string="Invoice Contact")
    date_invoice = fields.Date(
        string="Invoice Date",
        related="invoice_id.invoice_date",
        store=True,
        readonly=True,
    )

    _sql_constraints = [
        (
            "membership_year_uniq",
            "unique(membership_id, membership_year)",
            "Only one period per membership and year is allowed.",
        )
    ]

    @api.model
    def _default_membership_year(self):
        membership_id = self.env.context.get("default_membership_id")
        if membership_id:
            membership = self.env["membership.membership"].browse(membership_id)
            if membership.company_id:
                return membership.company_id._membership_period_year()
        return self.env.company._membership_period_year()

    @api.model
    def _normalize_membership_year_value(self, value):
        return normalize_year_value(value, self._fields["membership_year"].string)

    @api.depends("membership_year", "membership_id.date_start", "membership_id.date_end")
    def _compute_period_dates(self):
        """1 January - 31 December, clipped to the membership's own run."""
        for record in self:
            if not record.membership_year:
                record.date_start = record.date_end = False
                continue
            start = date(record.membership_year, 1, 1)
            end = date(record.membership_year, 12, 31)
            membership_start = record.membership_id.date_start
            membership_end = record.membership_id.date_end
            record.date_start = max(start, membership_start) if membership_start else start
            record.date_end = min(end, membership_end) if membership_end else end

    def _is_billed(self):
        """True once something has actually been applied to this period."""
        self.ensure_one()
        return bool(
            self.invoice_id
            or self.refund_move_id
            or self.amount_paid
            or self.date_paid
            or self.billing_status not in (False, "to_invoice", "waived")
        )

    def _refresh_unbilled_invoicing_strategy(self):
        """Re-derive the applied strategy of periods that were never billed.

        The field records what was *applied*. Until an invoice or a payment
        exists nothing has been, so a later company or membership change must
        still reach the period - otherwise a period created in manual mode
        silently suppresses the invoice the manager asked for. Billed and paid
        periods keep their value; imported history and decision 2.4 rely on it.
        """
        for record in self:
            if record._is_billed():
                continue
            strategy = record.membership_id._get_invoicing_strategy()
            if strategy != record.membership_invoicing_strategy:
                record.membership_invoicing_strategy = strategy

    @api.depends("amount")
    def _compute_is_free(self):
        for record in self:
            record.is_free = float(record.amount or 0.0) == 0.0

    @api.depends(
        "membership_invoicing_strategy",
        "invoice_id",
        "invoice_line_id.price_subtotal",
    )
    def _compute_amount_invoiced(self):
        for record in self:
            if record.invoice_id and record.invoice_line_id:
                record.amount_invoiced = record.invoice_line_id.price_subtotal
            elif record.membership_invoicing_strategy == "manual":
                # No invoice: keep what "Mark as Paid" or an import wrote.
                record.amount_invoiced = record.amount_invoiced or 0.0
            else:
                record.amount_invoiced = 0.0

    @api.depends(
        "membership_invoicing_strategy",
        "invoice_id",
        "invoice_id.state",
        "invoice_id.payment_state",
        "invoice_id.amount_total",
        "invoice_id.amount_residual",
        "amount_invoiced",
    )
    def _compute_amount_paid(self):
        for record in self:
            invoice = record.invoice_id
            if not invoice:
                if record.membership_invoicing_strategy != "manual":
                    record.amount_paid = 0.0
                else:
                    record.amount_paid = record.amount_paid or 0.0
                continue
            if not record.amount_invoiced:
                record.amount_paid = 0.0
                continue
            total = invoice.amount_total or 0.0
            if total:
                paid_ratio = max(
                    0.0,
                    min(1.0, (total - invoice.amount_residual) / total),
                )
            else:
                paid_ratio = 1.0 if invoice.payment_state in ("in_payment", "paid") else 0.0
            record.amount_paid = record.currency_id.round(record.amount_invoiced * paid_ratio)

    @api.depends(
        "membership_invoicing_strategy",
        "is_free",
        "invoice_id",
        "invoice_id.state",
        "invoice_id.payment_state",
        "refund_move_id",
        "refund_move_id.state",
    )
    def _compute_billing_status(self):
        """An invoice, when there is one, always wins - in every strategy.

        Only without an invoice do the strategies differ: `manual` keeps what a
        human (or the importer) set, the others report an open period.
        """
        for record in self:
            if record.refund_move_id and record.refund_move_id.state == "posted":
                record.billing_status = "refunded"
            elif record.invoice_id:
                if record.invoice_id.state == "cancel":
                    record.billing_status = "cancelled"
                elif record.invoice_id.payment_state in ("in_payment", "paid"):
                    record.billing_status = "paid"
                elif record.invoice_id.payment_state == "partial":
                    record.billing_status = "partial"
                else:
                    record.billing_status = "invoiced"
            elif (
                record.membership_invoicing_strategy == "manual"
                and record.billing_status not in (False, "to_invoice", "waived")
            ):
                # Keep what "Mark as Paid" or the importer set.
                continue
            elif record.is_free:
                record.billing_status = "waived"
            else:
                record.billing_status = "to_invoice"

    @api.model
    def action_open_default_year_periods(self):
        action = self.env.ref(
            "association_membership.action_membership_period"
        ).read()[0]
        default_year = self.env.company._membership_period_year()
        action["context"] = {
            "search_default_current_year": 1,
            "default_membership_year_filter": default_year,
        }
        return action

    @api.model
    def _prepare_membership_period_values(self, vals, membership=False):
        vals = vals.copy()
        membership = membership or self.env["membership.membership"].browse(vals["membership_id"])
        vals["membership_year"] = self._normalize_membership_year_value(
            vals.get("membership_year") or self._default_membership_year()
        )
        if "amount" not in vals:
            vals["amount"] = membership.amount or 0.0
        vals.setdefault("invoice_partner_id", membership._get_invoice_partner().id)
        vals.setdefault("product_id", membership.product_id.id)
        vals.setdefault(
            "membership_invoicing_strategy",
            membership._get_invoicing_strategy()
            if membership
            else self.env.company.membership_invoicing_strategy,
        )
        if vals.get("invoice_line_id") and not vals.get("invoice_id"):
            line = self.env["account.move.line"].browse(vals["invoice_line_id"])
            vals["invoice_id"] = line.move_id.id
        return vals

    @api.model
    def _prepare_membership_period_write_values(self, vals):
        vals = vals.copy()
        if "membership_year" in vals:
            vals["membership_year"] = self._normalize_membership_year_value(vals["membership_year"])
        if vals.get("invoice_line_id") and not vals.get("invoice_id"):
            line = self.env["account.move.line"].browse(vals["invoice_line_id"])
            vals["invoice_id"] = line.move_id.id
        return vals

    @api.constrains("company_id", "membership_id")
    def _check_company_matches_membership(self):
        for record in self:
            if record.company_id != record.membership_id.company_id:
                raise ValidationError(_("The period company must match the membership company."))

    @api.constrains("membership_id")
    def _check_membership_not_draft(self):
        # Draft is "not in force": it may keep the periods it had when it was
        # reverted, but gets no new ones until it is submitted again (15.12).
        for record in self:
            if record.membership_id.state == "draft":
                raise ValidationError(
                    _(
                        "Membership %s is still a draft. Submit it before adding"
                        " periods."
                    )
                    % record.membership_id.display_name
                )

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        if "membership_year" in fields_list and not defaults.get("membership_year"):
            defaults["membership_year"] = self._default_membership_year()
        membership_id = defaults.get("membership_id") or self.env.context.get("default_membership_id")
        if membership_id and "invoice_partner_id" in fields_list and not defaults.get("invoice_partner_id"):
            membership = self.env["membership.membership"].browse(membership_id)
            defaults["invoice_partner_id"] = membership._get_invoice_partner().id
        if membership_id and "product_id" in fields_list and not defaults.get("product_id"):
            defaults["product_id"] = self.env["membership.membership"].browse(membership_id).product_id.id
        return defaults

    @api.onchange("membership_id")
    def _onchange_membership_id(self):
        if not self.membership_id:
            return
        self.invoice_partner_id = self.membership_id._get_invoice_partner()
        self.product_id = self.membership_id.product_id
        if not self.membership_year:
            self.membership_year = self.membership_id.company_id._membership_period_year()

    @api.model_create_multi
    def create(self, vals_list):
        prepared_vals_list = [self._prepare_membership_period_values(vals) for vals in vals_list]
        return super().create(prepared_vals_list)

    def write(self, vals):
        vals = self._prepare_membership_period_write_values(vals)
        result = super().write(vals)
        if {"invoice_line_id", "invoice_id", "refund_move_id"} & set(vals):
            self._sync_accounting_links_from_lines()
        return result

    def _get_sale_journal(self, company):
        journal = self.env["account.journal"].with_company(company).search(
            [
                ("type", "=", "sale"),
                ("company_id", "=", company.id),
            ],
            limit=1,
        )
        if not journal:
            raise UserError(_("No sales journal was found for company %s.") % company.display_name)
        return journal

    def _create_membership_invoices(self, auto_post=False, invoice_date=False):
        eligible = self.filtered(
            lambda period: not period.is_free
            and not period.invoice_id
            and not period.invoice_line_id
            and not period.refund_move_id
        )
        invoices = self.env["account.move"]
        # An unbilled period follows the member's invoice contact; only an issued
        # invoice fixes it (15.26).
        for period in eligible:
            current = period.membership_id._get_invoice_partner()
            if period.invoice_partner_id != current:
                period.invoice_partner_id = current
        grouped = defaultdict(lambda: self.env["membership.period"])
        for period in eligible.sorted(key=lambda record: (record.membership_year, record.id)):
            group_key = (
                period.invoice_partner_id.id,
                period.company_id.id,
                period.membership_year,
                period.currency_id.id,
            )
            grouped[group_key] |= period

        for periods in grouped.values():
            company = periods[0].company_id
            invoice_vals = {
                "move_type": "out_invoice",
                "partner_id": periods[0].invoice_partner_id.id,
                "company_id": company.id,
                "currency_id": periods[0].currency_id.id,
                "journal_id": self._get_sale_journal(company).id,
                "invoice_date": invoice_date or fields.Date.context_today(self),
                "invoice_line_ids": [],
            }
            for period in periods.sorted(key=lambda record: record.id):
                invoice_vals["invoice_line_ids"].append(
                    Command.create(
                        {
                            "name": period.product_id.display_name,
                            "product_id": period.product_id.id,
                            "quantity": 1.0,
                            "price_unit": period.amount,
                            "membership_id": period.membership_id.id,
                            "membership_year": period.membership_year,
                        }
                    )
                )
            invoice = self.env["account.move"].with_company(company).create(invoice_vals)
            line_map = defaultdict(lambda: self.env["account.move.line"])
            for line in invoice.invoice_line_ids.filtered("membership_id"):
                line_map[(line.membership_id.id, line.membership_year)] |= line
            for period in periods:
                invoice_line = line_map[(period.membership_id.id, period.membership_year)]
                if len(invoice_line) != 1:
                    raise UserError(
                        _("Unable to match the generated invoice line for period %(year)s.")
                        % {"year": period.membership_year}
                    )
                invoice_line.write({"membership_period_id": period.id})
            if auto_post:
                invoice.action_post()
            invoices |= invoice
        return invoices

    def _apply_invoicing_strategy(self, invoice_date=False, force=False):
        """Invoice these periods according to the strategy frozen on each.

        `manual` creates nothing by itself; `force=True` (the "Create Invoice"
        action) makes it produce a draft invoice like `draft` does.
        """
        # A period that was never billed follows the current setting; this is
        # the moment the strategy is actually applied and therefore frozen.
        self._refresh_unbilled_invoicing_strategy()
        invoices = self.env["account.move"]
        grouped = defaultdict(lambda: self.env["membership.period"])
        for period in self:
            strategy = period.membership_invoicing_strategy or "manual"
            if strategy == "manual" and not force:
                continue
            grouped[(period.company_id, strategy)] |= period
        for (_company, strategy), periods in grouped.items():
            invoices |= periods._create_membership_invoices(
                auto_post=strategy == "confirm",
                invoice_date=invoice_date,
            )
        return invoices

    def _sync_accounting_links_from_lines(self):
        move_line_model = self.env["account.move.line"]
        move_lines = move_line_model.search(
            [
                ("membership_period_id", "in", self.ids),
                ("move_id.move_type", "in", ("out_invoice", "out_refund")),
            ]
        )
        grouped_lines = defaultdict(lambda: self.env["account.move.line"])
        for line in move_lines:
            grouped_lines[line.membership_period_id.id] |= line
        for record in self:
            lines = grouped_lines.get(record.id, self.env["account.move.line"])
            invoice_lines = lines.filtered(lambda line: line.move_id.move_type == "out_invoice")
            refund_lines = lines.filtered(lambda line: line.move_id.move_type == "out_refund")
            values = {
                "invoice_id": invoice_lines[:1].move_id.id if invoice_lines else False,
                "invoice_line_id": invoice_lines[:1].id if invoice_lines else False,
                "refund_move_id": refund_lines[:1].move_id.id if refund_lines else False,
            }
            super(MembershipPeriod, record).write(values)

    def post_refund_review_message(self, refund_move):
        for record in self:
            record.membership_id.message_post(
                body=_("A refund was posted for period %(year)s via %(refund)s.")
                % {
                    "year": record.membership_year,
                    "refund": refund_move.display_name,
                }
            )

    def _tax_receipt_partner(self):
        self.ensure_one()
        return (self.invoice_partner_id or self.membership_id._get_invoice_partner()).commercial_partner_id

    def _is_tax_receipt_eligible(self, invoice):
        self.ensure_one()
        # Only once the money is confirmed, not while the payment is in_payment (6.4).
        if self.tax_receipt_id or invoice.payment_state != "paid":
            return False
        if not self.product_id.tax_receipt_ok:
            return False
        return self._tax_receipt_partner().tax_receipt_option == "each"

    def _flag_invalid_tax_receipts(self, move):
        """A refund or reversed payment invalidates the receipt (6.4).

        donation_base receipts have no cancelled state, and a sent receipt must
        be reclaimed anyway: never delete, ask for a manual correction.
        """
        for receipt in self.tax_receipt_id:
            receipt.sudo().activity_schedule(
                "mail.mail_activity_data_todo",
                summary=_("Reclaim or correct this tax receipt"),
                note=_(
                    "%(move)s reverses a payment this receipt was issued for."
                    " Reclaim the receipt from the donor or issue a corrected one."
                )
                % {"move": move.display_name},
                user_id=self.env.uid,
            )

    def _prepare_tax_receipt_values(self, invoice):
        self.ensure_one()
        return {
            "company_id": self.company_id.id,
            "currency_id": self.company_id.currency_id.id,
            # The day the money came in, not the invoice date (15.5).
            "donation_date": self.date_paid or fields.Date.context_today(self),
            "amount": self.amount_paid or self.amount_invoiced or self.amount,
            "type": "each",
            "partner_id": self._tax_receipt_partner().id,
        }

    def _maybe_issue_tax_receipt(self, invoice):
        self.ensure_one()
        if not self._is_tax_receipt_eligible(invoice):
            return self.env["donation.tax.receipt"]
        receipt = self.env["donation.tax.receipt"].create(self._prepare_tax_receipt_values(invoice))
        self.tax_receipt_id = receipt.id
        self.membership_id.message_post(
            body=_("Tax receipt %(receipt)s issued for period %(year)s.")
            % {
                "receipt": receipt.display_name,
                "year": self.membership_year,
            }
        )
        return receipt

    def _drop_unbilled(self):
        """Cancel draft invoices and delete these periods.

        Used when a membership is cancelled and its open periods are not
        owed after all. A posted invoice is left alone - it needs a credit note,
        so its period stays as the record of that.
        """
        removable = self.env["membership.period"]
        for record in self:
            invoice = record.invoice_id
            if invoice and invoice.state == "posted":
                record.membership_id.message_post(
                    body=_(
                        "Period %(year)s was kept: invoice %(invoice)s is"
                        " posted and needs a credit note."
                    )
                    % {"year": record.membership_year, "invoice": invoice.display_name}
                )
                continue
            if invoice and invoice.state == "draft":
                invoice.button_cancel()
            removable |= record
        for record in removable:
            record.membership_id.message_post(
                body=_("Period %s was removed when the membership was cancelled.")
                % record.membership_year
            )
        removable.unlink()
        return True

    def action_create_invoice(self):
        """Invoice these periods now, whatever the strategy.

        `manual` and `draft` leave the invoice in draft, `confirm` posts it.
        """
        blocked = self.filtered(
            lambda record: record.is_free
            or record.invoice_id
            or record.invoice_line_id
            or record.refund_move_id
        )
        if blocked:
            raise UserError(
                _(
                    "These periods cannot be invoiced: %s."
                    " Free periods, and periods that already have an"
                    " invoice or a refund, are skipped."
                )
                % ", ".join(
                    "%s %s" % (record.membership_id.display_name, record.membership_year)
                    for record in blocked
                )
            )
        invoices = self._apply_invoicing_strategy(force=True)
        if not invoices:
            return True
        return {
            "type": "ir.actions.act_window",
            "name": _("Membership Invoices"),
            "res_model": "account.move",
            "view_mode": "form" if len(invoices) == 1 else "list,form",
            "res_id": invoices.id if len(invoices) == 1 else False,
            "domain": [("id", "in", invoices.ids)],
        }

    def _check_manual_payment_allowed(self):
        for record in self:
            if record.membership_invoicing_strategy != "manual":
                raise UserError(
                    _(
                        "Period %(name)s %(year)s was created with the"
                        " %(strategy)s strategy. Only periods created in"
                        " manual mode can be paid by hand."
                    )
                    % {
                        "name": record.membership_id.display_name,
                        "year": record.membership_year,
                        "strategy": record.membership_invoicing_strategy or "-",
                    }
                )
            if record.invoice_id:
                raise UserError(
                    _(
                        "Period %(name)s %(year)s has invoice %(invoice)s."
                        " Register the payment on the invoice; the period"
                        " follows it automatically."
                    )
                    % {
                        "name": record.membership_id.display_name,
                        "year": record.membership_year,
                        "invoice": record.invoice_id.display_name,
                    }
                )

    def action_mark_as_paid(self):
        self._check_manual_payment_allowed()
        for record in self:
            record.write({
                "amount_invoiced": record.amount,
                "amount_paid": record.amount,
                "billing_status": "paid",
                "date_paid": record.date_paid or fields.Date.context_today(record),
            })
        return True

    def action_unmark_as_paid(self):
        """Undo "Mark as Paid" - the inverse a mistyped payment needs."""
        self._check_manual_payment_allowed()
        for record in self:
            if record.tax_receipt_id:
                raise UserError(
                    _(
                        "Tax receipt %(receipt)s was issued for period"
                        " %(year)s. Reclaim or correct the receipt first."
                    )
                    % {
                        "receipt": record.tax_receipt_id.display_name,
                        "year": record.membership_year,
                    }
                )
            record.write({
                "amount_invoiced": 0.0,
                "amount_paid": 0.0,
                "date_paid": False,
                "billing_status": "waived" if record.is_free else "to_invoice",
            })
        return True
