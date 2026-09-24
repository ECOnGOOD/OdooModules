"""Move existing databases onto the shared member number counter.

Until 18.0.6.0.0 every company counted on its own, seeded from the shared
sequence but never drawing from it. Companies now share that counter unless
``member_number_own_sequence`` is set, and the field defaults to off — so the
shared counter must be lifted above every number those per-company counters
already issued, or the first new member collides with an existing one.

Idempotent: it only ever raises the shared counter.
"""

from odoo import SUPERUSER_ID, api

SEQUENCE_CODE = "association.membership.number.seq"


def _raise_shared_counter(env):
    sequence_model = env["ir.sequence"].sudo()
    shared = sequence_model.search(
        [("code", "=", SEQUENCE_CODE), ("company_id", "=", False)], limit=1
    )
    if not shared:
        return

    per_company = sequence_model.search(
        [("code", "=", SEQUENCE_CODE), ("company_id", "!=", False)]
    )
    if not per_company:
        return

    highest = max(per_company.mapped("number_next_actual"))
    if highest > shared.number_next_actual:
        # write() rather than assignment: on a standard sequence the ALTER
        # SEQUENCE only runs on flush, and a later search would not trigger it.
        shared.write({"number_next": highest})


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    _raise_shared_counter(env)
