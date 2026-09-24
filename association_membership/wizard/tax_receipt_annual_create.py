from odoo import _, api, fields, models
from odoo.tools.misc import format_date


class TaxReceiptAnnualCreate(models.TransientModel):
    _inherit = "tax.receipt.annual.create"

    partner_ids = fields.Many2many(
        "res.partner",
        string="Only These Donors",
        domain=[("parent_id", "=", False)],
        help="Empty: every donor with receipt-eligible payments in the range.",
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    # A dry run in the dialog (15.3): what "Generate" would create.
    preview_donor_count = fields.Integer(
        string="Donors",
        compute="_compute_preview",
    )
    preview_period_count = fields.Integer(
        string="Periods",
        compute="_compute_preview",
    )
    preview_amount = fields.Monetary(
        string="Total",
        compute="_compute_preview",
    )
    skipped_receipt_ids = fields.Many2many(
        "donation.tax.receipt",
        string="Skipped",
        compute="_compute_preview",
        help="These donors already have an annual receipt in the range. They are"
        " skipped; correct or delete that receipt first to include them.",
    )

    @api.depends("start_date", "end_date", "company_id", "partner_ids")
    def _compute_preview(self):
        for wizard in self:
            receipts_by_partner = {}
            skipped = self.env["donation.tax.receipt"]
            if wizard.start_date and wizard.end_date and wizard.company_id:
                receipts_by_partner, skipped = wizard._dry_run()
            wizard.preview_donor_count = len(receipts_by_partner)
            wizard.preview_period_count = len(wizard._period_ids(receipts_by_partner))
            wizard.preview_amount = sum(values["amount"] for values in receipts_by_partner.values())
            wizard.skipped_receipt_ids = skipped

    def _existing_annual_receipts(self):
        """The receipts donation_base aborts on; the same domain."""
        self.ensure_one()
        return self.env["donation.tax.receipt"].search(
            [
                ("donation_date", "<=", self.end_date),
                ("donation_date", ">=", self.start_date),
                ("company_id", "=", self.company_id.id),
                ("type", "=", "annual"),
            ]
        )

    def _dry_run(self):
        """What "Generate" would create, by donor, and the receipts it skips."""
        self.ensure_one()
        eligible = self._annual_receipt_dict(skip_existing=False)
        skipped = self._existing_annual_receipts().filtered(
            lambda receipt: receipt.partner_id in eligible
        )
        receipts_by_partner = {
            partner: values for partner, values in eligible.items() if partner not in skipped.partner_id
        }
        return receipts_by_partner, skipped

    def _annual_context(self, skip_existing=True):
        self.ensure_one()
        return {
            "tax_receipt_annual_partner_ids": self.partner_ids._origin.ids,
            "tax_receipt_annual_skip_partner_ids": (
                self._existing_annual_receipts().partner_id.ids if skip_existing else []
            ),
        }

    def _annual_receipt_dict(self, skip_existing=True):
        self.ensure_one()
        receipts_by_partner = {}
        self.env["donation.tax.receipt"].with_context(
            **self._annual_context(skip_existing)
        ).update_tax_receipt_annual_dict(
            receipts_by_partner, self.start_date, self.end_date, self.company_id
        )
        return receipts_by_partner

    @api.model
    def _period_ids(self, receipts_by_partner):
        return [
            command[1]
            for values in receipts_by_partner.values()
            for command in values["extra_vals"].get("membership_period_ids", [])
        ]

    def action_preview(self):
        """The periods "Generate" would receipt, grouped by member."""
        self.ensure_one()
        action = self.env.ref("association_membership.action_membership_period").read()[0]
        action.update(
            {
                "name": _("Annual Receipts Preview: %(start)s - %(end)s")
                % {
                    "start": format_date(self.env, self.start_date),
                    "end": format_date(self.env, self.end_date),
                },
                "domain": [("id", "in", self._period_ids(self._dry_run()[0]))],
                "context": {"create": False, "group_by": ["partner_id"]},
                "target": "current",
            }
        )
        return action

    @api.model
    def _prepare_annual_tax_receipt(self, partner, partner_dict):
        vals = super()._prepare_annual_tax_receipt(partner, partner_dict)
        # Issued today (15.34). donation_date stays the end of the range: the
        # receipt covers a period, and the PDF prints it.
        vals["date"] = fields.Date.context_today(self)
        return vals

    def generate_annual_receipts(self):
        """Skip donors that already have an annual receipt (D24), and explain
        an empty run instead of donation_base's bare UserError.

        Odoo titles every UserError "Invalid Operation", which reads like a
        fault when nothing was due. The dialog stays open so the range can be
        changed.
        """
        self.ensure_one()
        receipts_by_partner, skipped = self._dry_run()
        skipped_note = ""
        if skipped:
            skipped_note = _(
                "Skipped, because they already have an annual receipt in this range: %s."
            ) % ", ".join(
                "%s (%s)" % (receipt.partner_id.display_name, receipt.number) for receipt in skipped
            )
        if not receipts_by_partner:
            message = _(
                "No paid, receipt-eligible membership periods for %(company)s"
                " between %(start)s and %(end)s. A period counts when its"
                " product has Tax Receipt set, the donor's receipt option is"
                " Annual (or For Each Donation in manual mode) and it was"
                " paid in this range."
            ) % {
                "company": self.company_id.display_name,
                "start": format_date(self.env, self.start_date),
                "end": format_date(self.env, self.end_date),
            }
            return self._notification(
                _("No annual tax receipts to create"),
                " ".join(filter(None, [message, skipped_note])),
            )
        wizard = self.with_context(**self._annual_context())
        action = super(TaxReceiptAnnualCreate, wizard).generate_annual_receipts()
        if skipped:
            return self._notification(_("Some donors were skipped"), skipped_note, next_action=action)
        return action

    def _notification(self, title, message, next_action=None):
        params = {
            "type": "warning",
            "sticky": True,
            "title": title,
            "message": message,
        }
        if next_action:
            params["next"] = next_action
        return {"type": "ir.actions.client", "tag": "display_notification", "params": params}
