import logging

_logger = logging.getLogger(__name__)


def migrate(cr, version):
    """Clear OCA's manual "Invoice address", now hidden (15.26).

    Only values that point at an invoice-type child of the same partner are
    cleared: address_get then finds that child anyway, so nothing changes for
    invoicing. Any other value is left and logged for a person to look at.
    """
    cr.execute(
        """
        UPDATE res_partner p
           SET partner_invoice_id = NULL
          FROM res_partner c
         WHERE c.id = p.partner_invoice_id
           AND c.parent_id = p.id
           AND c.type = 'invoice'
     RETURNING p.id
        """
    )
    _logger.info("Cleared the OCA invoice address on partners %s.", [row[0] for row in cr.fetchall()])
    cr.execute("SELECT id FROM res_partner WHERE partner_invoice_id IS NOT NULL")
    remaining = [row[0] for row in cr.fetchall()]
    if remaining:
        _logger.warning(
            "Partners %s still have an OCA invoice address that is not their own"
            " invoice-type child. It is hidden now; check them by hand.",
            remaining,
        )
