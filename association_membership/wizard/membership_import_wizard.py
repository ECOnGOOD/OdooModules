import base64
import csv
import io

from odoo import Command, _, fields, models
from odoo.exceptions import ValidationError

try:
    import openpyxl
except ImportError:  # pragma: no cover
    openpyxl = None


class DryRunRollback(Exception):
    pass


class MembershipImportWizard(models.TransientModel):
    _name = "membership.import.wizard"
    _description = "Membership Import Wizard"

    file = fields.Binary(required=True)
    filename = fields.Char()
    delimiter = fields.Char(default=",")
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
    )
    dry_run = fields.Boolean()
    result_line_ids = fields.One2many(
        "membership.import.wizard.line",
        "wizard_id",
        string="Results",
    )

    def _decode_rows(self):
        self.ensure_one()
        content = base64.b64decode(self.file or b"")
        filename = (self.filename or "").lower()
        if filename.endswith(".xlsx"):
            if openpyxl is None:
                raise ValidationError(_("XLSX import requires openpyxl to be available."))
            workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
            worksheet = workbook.active
            rows = list(worksheet.iter_rows(values_only=True))
            headers = [str(header or "").strip() for header in rows[0]]
            return [dict(zip(headers, row)) for row in rows[1:] if any(row)]
        text_stream = io.StringIO(content.decode("utf-8-sig"))
        reader = csv.DictReader(text_stream, delimiter=self.delimiter or ",")
        return [row for row in reader]

    def _parse_bool(self, value):
        if value in (None, "", False):
            return False
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        return normalized in {"1", "true", "yes", "y", "on"}

    def _parse_int(self, value, field_label):
        if value in (None, "", False):
            return False
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Invalid %s: %s") % (field_label, value)) from error

    def _parse_float(self, value, field_label):
        if value in (None, "", False):
            return False
        try:
            return float(value)
        except (TypeError, ValueError) as error:
            raise ValidationError(_("Invalid %s: %s") % (field_label, value)) from error

    def _parse_date(self, value, field_label):
        if value in (None, "", False):
            return False
        return fields.Date.to_date(value)

    def _find_or_create_partner(self, external_ref, name):
        partner_model = self.env["res.partner"].with_context(active_test=False)
        partner = self.env["res.partner"]
        if external_ref:
            partner = partner_model.search([("ref", "=", external_ref)], limit=1)
        elif name:
            candidates = partner_model.search([("name", "=", name)])
            if len(candidates) > 1:
                raise ValidationError(_("Ambiguous partner match for '%s'.") % name)
            partner = candidates[:1]
        if partner:
            update_vals = {}
            if external_ref and not partner.ref:
                update_vals["ref"] = external_ref
            if name and not partner.name:
                update_vals["name"] = name
            if update_vals:
                partner.write(update_vals)
            return partner
        if not name:
            raise ValidationError(_("A partner name is required when no existing partner can be matched."))
        return partner_model.create({"name": name, "ref": external_ref or False})

    def _find_membership_product(self, row):
        code = row.get("product_code") or row.get("product_default_code")
        name = row.get("product_name")
        category = self.company_id._membership_product_category()
        if not category:
            raise ValidationError(
                _("Configure a membership product category for company %s first.")
                % self.company_id.display_name
            )
        product_model = self.env["product.product"]
        domain = [
            ("categ_id", "child_of", category.id),
            "|",
            ("company_id", "=", False),
            ("company_id", "=", self.company_id.id),
        ]
        if code:
            products = product_model.search(domain + [("default_code", "=", code)])
        elif name:
            products = product_model.search(domain + [("name", "=", name)])
        else:
            raise ValidationError(_("A membership product identifier is required."))
        if not products:
            raise ValidationError(_("No membership product matched the import row."))
        if len(products) == 1:
            return products

        company_products = products.filtered(lambda product: product.company_id == self.company_id)
        if len(company_products) == 1:
            return company_products

        identifier = code or name
        raise ValidationError(
            _("Ambiguous membership product match for '%s'.") % identifier
        )

    def _find_membership(self, row, partner, product, date_start):
        membership_model = self.env["membership.membership"].with_context(active_test=False)
        external_ref = row.get("external_ref")
        if external_ref:
            membership = membership_model.search(
                [
                    ("external_ref", "=", external_ref),
                    ("company_id", "=", self.company_id.id),
                ],
                limit=1,
            )
            if membership:
                return membership
        return membership_model.search(
            [
                ("partner_id", "=", partner.id),
                ("company_id", "=", self.company_id.id),
                ("product_id", "=", product.id),
                ("date_start", "=", date_start),
            ],
            limit=1,
        )

    def _apply_row(self, row):
        partner = self._find_or_create_partner(
            row.get("partner_external_ref"),
            row.get("partner_name"),
        )
        invoice_partner_ref = row.get("invoice_partner_external_ref")
        invoice_partner_name = row.get("invoice_partner_name") or row.get("invoice_partner")
        invoice_partner = (
            self._find_or_create_partner(invoice_partner_ref, invoice_partner_name)
            if invoice_partner_ref or invoice_partner_name
            else partner
        )
        product = self._find_membership_product(row)
        date_start = self._parse_date(row.get("date_start"), _("start date"))
        if not date_start:
            raise ValidationError(_("Each imported membership row requires a start date."))

        membership = self._find_membership(row, partner, product, date_start)
        create_vals = {
            "partner_id": partner.id,
            "invoice_partner_id": invoice_partner.id,
            "company_id": self.company_id.id,
            "product_id": product.id,
            "date_start": date_start,
        }
        if row.get("external_ref"):
            create_vals["external_ref"] = row.get("external_ref")
        state = row.get("state") or "waiting"
        state_values = {}
        if state == "cancelled":
            state_values = {
                "date_cancelled": self._parse_date(row.get("date_cancelled"), _("cancel date"))
                or fields.Date.context_today(self),
                "date_end": self._parse_date(row.get("date_end"), _("end date")),
                "cancel_reason": row.get("cancel_reason") or False,
            }
        if membership:
            membership.write(create_vals)
            if state and membership.state != state:
                membership._do_transition(state, **state_values)
            membership_status = "updated"
            membership_message = _("Updated membership.")
        else:
            create_vals["state"] = state
            create_vals.update(state_values)
            membership = self.env["membership.membership"].create(create_vals)
            membership_status = "created"
            membership_message = _("Created membership.")

        contribution_message = False
        membership_year = self._parse_int(row.get("membership_year"), _("membership year"))
        if membership_year:
            contribution_vals = {
                "membership_id": membership.id,
                "membership_year": membership_year,
            }
            if row.get("amount_expected") not in (None, "", False):
                contribution_vals["amount_expected"] = self._parse_float(
                    row.get("amount_expected"),
                    _("expected amount"),
                )
            if row.get("is_free") not in (None, "", False):
                contribution_vals["is_free"] = self._parse_bool(row.get("is_free"))
            contribution = self.env["membership.contribution"].search(
                [
                    ("membership_id", "=", membership.id),
                    ("membership_year", "=", membership_year),
                ],
                limit=1,
            )
            if contribution:
                contribution.write(contribution_vals)
                membership_status = "updated"
                contribution_message = _("Updated contribution for %s.") % membership_year
            else:
                self.env["membership.contribution"].create(contribution_vals)
                contribution_message = _("Created contribution for %s.") % membership_year

        return membership, membership_status, membership_message, contribution_message

    def action_run(self):
        self.ensure_one()
        self.result_line_ids.unlink()
        result_commands = [Command.clear()]

        for row_number, row in enumerate(self._decode_rows(), start=2):
            try:
                with self.env.cr.savepoint():
                    (
                        membership,
                        membership_status,
                        membership_message,
                        contribution_message,
                    ) = self._apply_row(row)
                    result_commands.append(
                        Command.create(
                            {
                                "row_number": row_number,
                                "status": membership_status,
                                "external_ref": row.get("external_ref"),
                                "partner_name": membership.partner_id.display_name,
                                "message": " ".join(
                                    message
                                    for message in [membership_message, contribution_message]
                                    if message
                                ),
                            }
                        )
                    )
                    if self.dry_run:
                        raise DryRunRollback()
            except DryRunRollback:
                self.env.invalidate_all()
            except Exception as error:
                self.env.invalidate_all()
                result_commands.append(
                    Command.create(
                        {
                            "row_number": row_number,
                            "status": "error",
                            "external_ref": row.get("external_ref"),
                            "partner_name": row.get("partner_name"),
                            "message": str(error),
                        }
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


class MembershipImportWizardLine(models.TransientModel):
    _name = "membership.import.wizard.line"
    _description = "Membership Import Wizard Result"

    wizard_id = fields.Many2one(
        "membership.import.wizard",
        required=True,
        ondelete="cascade",
    )
    row_number = fields.Integer(readonly=True)
    status = fields.Selection(
        [
            ("created", "Created"),
            ("updated", "Updated"),
            ("error", "Error"),
        ],
        required=True,
        readonly=True,
    )
    external_ref = fields.Char(readonly=True)
    partner_name = fields.Char(readonly=True)
    message = fields.Char(readonly=True)
