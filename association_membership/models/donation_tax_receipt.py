from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class DonationTaxReceipt(models.Model):
    _inherit = "donation.tax.receipt"

    membership_period_ids = fields.One2many(
        "membership.period",
        "tax_receipt_id",
        string="Membership Periods",
        readonly=True,
    )
    membership_period_count = fields.Integer(
        compute="_compute_membership_period_count",
    )

    @api.depends("membership_period_ids")
    def _compute_membership_period_count(self):
        for receipt in self:
            receipt.membership_period_count = len(receipt.membership_period_ids)

    def action_view_membership_periods(self):
        self.ensure_one()
        action = self.env.ref("association_membership.action_membership_period").read()[0]
        action["domain"] = [("tax_receipt_id", "=", self.id)]
        action["context"] = {"create": False}
        return action

    @api.model_create_multi
    def create(self, vals_list):
        company_ids = {vals.get("company_id") or self.env.company.id for vals in vals_list}
        for company in self.env["res.company"].browse(company_ids):
            company._ensure_tax_receipt_sequence()
        return super().create(vals_list)

    def action_send_tax_receipt(self):
        """Send one or several receipts with the company's template (15.2).

        Comment mode also for several: one email per receipt, each with its
        own PDF and logged on that receipt.
        """
        if not self:
            return False
        if len(self.company_id) > 1:
            raise UserError(_("Send the receipts of one company at a time."))
        missing = self.partner_id.filtered(lambda partner: not partner.email)
        if missing:
            raise UserError(
                _("Missing email on: %s.") % ", ".join(missing.mapped("display_name"))
            )
        template = self.company_id._get_membership_tax_receipt_template()
        return {
            "name": _("Send Tax Receipts") if len(self) > 1 else _("Send Tax Receipt"),
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "target": "new",
            "context": {
                "default_model": self._name,
                "default_res_ids": self.ids,
                "default_template_id": template.id,
                "default_composition_mode": "comment",
                "default_email_layout_xmlid": "mail.mail_notification_light",
                "force_email": True,
            },
        }

    @api.model
    def _membership_annual_periods(self, company, start_date, end_date):
        """Paid, receipt-eligible periods of the range without a receipt.

        A period belongs to the year it was paid (15.5): its payment date, set
        by "Mark as Paid" or when its invoice became paid, lies in the range.
        Imported history has no payment date and is never included.
        """
        periods = self.env["membership.period"].search(
            [
                ("company_id", "=", company.id),
                ("product_id.tax_receipt_ok", "=", True),
                ("tax_receipt_id", "=", False),
                ("date_paid", ">=", start_date),
                ("date_paid", "<=", end_date),
                "|",
                "&",
                ("membership_invoicing_strategy", "=", "manual"),
                ("billing_status", "=", "paid"),
                ("invoice_id.payment_state", "=", "paid"),
            ]
        )
        # Manual mode issues no per-payment receipts (decision 5), so partners
        # who chose "each" get their manually paid fees on the annual receipt.
        return periods.filtered(
            lambda period: period._tax_receipt_partner().tax_receipt_option == "annual"
            or (
                period.membership_invoicing_strategy == "manual"
                and period._tax_receipt_partner().tax_receipt_option == "each"
            )
        )

    @api.model
    def update_tax_receipt_annual_dict(self, tax_receipt_annual_dict, start_date, end_date, company):
        super().update_tax_receipt_annual_dict(
            tax_receipt_annual_dict, start_date, end_date, company
        )
        for period in self._membership_annual_periods(company, start_date, end_date):
            partner_dict = tax_receipt_annual_dict.setdefault(
                period._tax_receipt_partner(),
                {"amount": 0.0, "extra_vals": {}},
            )
            # Paid periods always carry amount_paid, in both modes.
            partner_dict["amount"] += period.amount_paid
            # donation_base passes extra_vals to the receipt it creates.
            partner_dict["extra_vals"].setdefault("membership_period_ids", []).append(
                Command.link(period.id)
            )
        # Set by the annual wizard: only the chosen donors, and none that
        # already has an annual receipt in the range (15.3).
        only = self.env.context.get("tax_receipt_annual_partner_ids")
        skip = self.env.context.get("tax_receipt_annual_skip_partner_ids") or []
        for partner in list(tax_receipt_annual_dict):
            if (only and partner.id not in only) or partner.id in skip:
                del tax_receipt_annual_dict[partner]
