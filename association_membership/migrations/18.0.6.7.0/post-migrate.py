"""Names without the product reference (16.4); "Member Of" follows the membership (16.8).

1. ``membership.name`` is stored: recompute it, so existing memberships lose
   the ``[MEM_…]`` reference of their product.
2. Re-sync the "Member Of" relation of every live membership, so that a
   cancelled membership's relation gets its end date and every relation starts
   with the earliest live membership.

Company visibility (16.7) is applied to new and changed memberships only;
existing partners keep the companies they have.

Idempotent.
"""

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return
    env = api.Environment(cr, SUPERUSER_ID, {})
    Membership = env["membership.membership"].with_context(active_test=False)
    memberships = Membership.search([])
    env.add_to_compute(memberships._fields["name"], memberships)
    memberships.flush_recordset(["name"])
    Membership.search([("state", "in", ("active", "cancelled"))])._sync_optional_partner_relations()
