"""One-off data changes for 18.0.2.8.0.

Formerly run by ``membership.membership._auto_init`` on every module update.
"""
import datetime

from odoo import SUPERUSER_ID, api
from odoo.exceptions import ValidationError
from odoo.tools.sql import column_exists


def _flag_membership_products(env):
    """``membership_ok`` replaces recognising membership products by category.

    The company setting ``membership_product_category_id`` is gone from the
    model; its column is still in the database.
    """
    category_ids = []
    if column_exists(env.cr, "res_company", "membership_product_category_id"):
        env.cr.execute(
            "SELECT DISTINCT membership_product_category_id FROM res_company"
            " WHERE membership_product_category_id IS NOT NULL"
        )
        category_ids = [row[0] for row in env.cr.fetchall()]
    default_category = env.ref(
        "association_membership.product_category_membership", raise_if_not_found=False
    )
    if default_category:
        category_ids.append(default_category.id)
    if category_ids:
        env["product.template"].with_context(active_test=False).search(
            [("categ_id", "child_of", category_ids)]
        ).membership_ok = True


def _split_active_with_end_date(cr):
    cr.execute(
        """
        UPDATE membership_membership
           SET state = CASE
               WHEN date_end > %s THEN 'cancelled'
               ELSE 'terminated'
           END
         WHERE state = 'active'
           AND date_end IS NOT NULL
        """,
        (datetime.date.today(),),
    )


def _migrate_legacy_membership_numbers(cr):
    cr.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'membership_membership'
           AND column_name IN ('member_number', 'external_ref')
        """
    )
    available_columns = {row[0] for row in cr.fetchall()}

    if "member_number" in available_columns:
        cr.execute(
            """
            UPDATE membership_membership
               SET membership_number = NULLIF(BTRIM(member_number), '')
             WHERE (membership_number IS NULL OR BTRIM(membership_number) = '')
               AND member_number IS NOT NULL
               AND BTRIM(member_number) != ''
            """
        )

    if "external_ref" not in available_columns:
        return

    cr.execute(
        """
        SELECT id,
               NULLIF(BTRIM(membership_number), '') AS membership_number,
               NULLIF(BTRIM(external_ref), '') AS external_ref
          FROM membership_membership
        """
    )
    existing_numbers = set()
    pending_numbers = {}
    for row in cr.dictfetchall():
        if row["membership_number"]:
            existing_numbers.add(row["membership_number"])
        elif row["external_ref"]:
            pending_numbers.setdefault(row["external_ref"], []).append(row["id"])

    for number, ids in pending_numbers.items():
        if len(ids) > 1:
            raise ValidationError(
                "Cannot migrate legacy external references because '%s' is used on"
                " multiple memberships." % number
            )
        if number in existing_numbers:
            raise ValidationError(
                "Cannot migrate legacy external references because '%s' is already"
                " used as a membership number." % number
            )

    cr.execute(
        """
        UPDATE membership_membership
           SET membership_number = NULLIF(BTRIM(external_ref), '')
         WHERE (membership_number IS NULL OR BTRIM(membership_number) = '')
           AND external_ref IS NOT NULL
           AND BTRIM(external_ref) != ''
        """
    )


def migrate(cr, version):
    if not version:
        return
    _split_active_with_end_date(cr)
    _migrate_legacy_membership_numbers(cr)
    _flag_membership_products(api.Environment(cr, SUPERUSER_ID, {}))
