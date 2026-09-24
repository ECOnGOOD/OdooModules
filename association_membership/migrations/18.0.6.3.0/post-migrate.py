"""Member numbers from ir.sequence alone (15.22, D31); contacts from the member (15.26).

1. The default sequence (no company) carries the whole format: prefix
   ``MEM/%(year)s/``, 5 digits, ``no_gap``. Its counter is lifted above every
   company counter, so no number issued so far comes round again.
2. Every company switches to the default numbering: own sequences are archived.
   Companies that had their own counter or a prefix of their own are logged, so
   each can decide whether to switch own numbering back on.
3. ``membership.invoice_partner_id`` became a computed field; stored values are
   recomputed from the members.

Idempotent.
"""

import logging

from odoo import SUPERUSER_ID, api

_logger = logging.getLogger(__name__)

SEQUENCE_CODE = "association.membership.number.seq"
DEFAULT_PREFIX = "MEM/%(year)s/"


def _log_company_formats(cr):
    cr.execute(
        "SELECT column_name FROM information_schema.columns"
        " WHERE table_name = 'res_company' AND column_name = 'member_number_prefix'"
    )
    if not cr.fetchone():
        return
    cr.execute(
        "SELECT id, name, member_number_prefix FROM res_company"
        " WHERE COALESCE(member_number_prefix, '') NOT IN ('', %s)",
        [DEFAULT_PREFIX],
    )
    for company_id, name, prefix in cr.fetchall():
        _logger.warning(
            "Company %s (%s) used the member number prefix %r; it now uses the"
            " default numbering. Switch own numbering on for it if it should keep its format.",
            company_id, name, prefix,
        )


def _switch_to_default_numbering(env):
    sequences = env["ir.sequence"].sudo().with_context(active_test=False)
    default = env["res.company"]._default_membership_number_sequence()
    own = sequences.search([("code", "=", SEQUENCE_CODE), ("company_id", "!=", False)])
    highest = max([default.number_next_actual, *own.mapped("number_next_actual")])
    vals = {"prefix": DEFAULT_PREFIX, "suffix": False, "padding": 5}
    if default.implementation != "no_gap":
        # number_next is stale on a standard sequence; the real value goes along.
        vals.update(implementation="no_gap", number_next=highest)
    elif highest > default.number_next_actual:
        vals["number_next"] = highest
    default.write(vals)
    for sequence in own.filtered("active"):
        _logger.warning(
            "Company %s (%s) had its own member number counter; it now uses the"
            " default numbering (its sequence %s is archived).",
            sequence.company_id.id, sequence.company_id.name, sequence.id,
        )
    own.write({"active": False})


def _recompute_contacts(env):
    memberships = env["membership.membership"].with_context(active_test=False).search([])
    env.add_to_compute(memberships._fields["invoice_partner_id"], memberships)
    memberships.flush_recordset(["invoice_partner_id"])


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _log_company_formats(cr)
    _switch_to_default_numbering(env)
    _recompute_contacts(env)
