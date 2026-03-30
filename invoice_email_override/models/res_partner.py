import re

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError

SIMPLE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ResPartner(models.Model):
    _inherit = "res.partner"

    invoice_email = fields.Char(
        string="Invoice Email",
        help=(
            "When set on a company contact, invoice emails are sent to this address "
            "instead of the regular email."
        ),
    )

    @api.constrains("invoice_email")
    def _check_invoice_email_format(self):
        for partner in self:
            if not partner.invoice_email:
                continue
            email = partner.invoice_email.strip()
            split_fn = getattr(tools, "email_split", None)
            if split_fn:
                is_valid = len(split_fn(email)) == 1
            else:
                is_valid = bool(SIMPLE_EMAIL_RE.match(email))
            if not is_valid:
                raise ValidationError(
                    _("%s is not a valid invoice email address.", partner.invoice_email)
                )
