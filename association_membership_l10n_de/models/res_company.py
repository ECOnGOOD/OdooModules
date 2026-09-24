from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    de_finanzamt_name = fields.Char(
        string="Finanzamt",
        help="Name of the tax office that issued the Freistellungsbescheid.",
    )
    de_steuernummer = fields.Char(
        string="Steuernummer",
        help="Tax number assigned by the Finanzamt.",
    )
    de_freistellungsbescheid_date = fields.Date(
        string="Date of Freistellungsbescheid / Anlage",
    )
    de_freistellungsbescheid_year = fields.Char(
        string="Veranlagungszeitraum",
        help="Tax assessment period covered by the Freistellungsbescheid (e.g. '2023' or '2021-2023').",
    )
    de_charitable_purpose = fields.Char(
        string="Förderungszweck",
        help=(
            "Charitable purpose covered by the Satzung "
            "(e.g. 'Förderung des Naturschutzes', 'Förderung von Wissenschaft und Forschung')."
        ),
    )
    de_signatory_name = fields.Char(
        string="Receipt Signatory Name",
    )
    de_signatory_role = fields.Char(
        string="Receipt Signatory Role",
        help="e.g. Vorstand, Schatzmeister.",
    )
    de_receipt_is_membership = fields.Boolean(
        string="Receipts cover Mitgliedsbeiträge",
        default=True,
        help=(
            "If enabled, receipts include the Mitgliedsbeitrag wording. "
            "Disable for one-off donation receipts."
        ),
    )

    def _is_german_receipt_issuer(self):
        return self.partner_id.country_id.code == "DE"

    def _default_membership_tax_receipt_template(self):
        if self._is_german_receipt_issuer():
            template = self.env.ref(
                "association_membership_l10n_de.mail_template_zuwendungsbestaetigung",
                raise_if_not_found=False,
            )
            if template:
                return template
        return super()._default_membership_tax_receipt_template()

    def _get_membership_tax_receipt_report(self):
        if self._is_german_receipt_issuer():
            return self.env.ref("association_membership_l10n_de.report_zuwendungsbestaetigung")
        return super()._get_membership_tax_receipt_report()
