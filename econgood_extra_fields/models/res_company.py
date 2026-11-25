from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Set per association by the bootstrap (D81); empty counts as informal.
    letter_salutation_style = fields.Selection(
        [
            ("informal", "Informal (Liebe Frau / Lieber Herr)"),
            ("formal", "Formal (Sehr geehrte Frau / Sehr geehrter Herr)"),
        ],
        string="Letter Salutation Style",
        help="How the letter salutation of new contacts is filled from their gender.",
    )
