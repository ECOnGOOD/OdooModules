"""Rename membership.contribution to membership.period.

Runs before the new module definition is loaded, so the ORM finds the data
where it expects it and does not drop and recreate the table. Nothing here is
conditional on the module version beyond Odoo's own migration dispatch; every
statement checks the current shape first so the script is safe to re-run.
"""
import logging

_logger = logging.getLogger(__name__)

# (old table, new table)
TABLE_RENAMES = [("membership_contribution", "membership_period")]

# (table, old column, new column)
COLUMN_RENAMES = [
    ("account_move_line", "membership_contribution_id", "membership_period_id"),
    (
        "res_company",
        "membership_default_contribution_year",
        "membership_default_period_year",
    ),
    ("membership_membership", "last_contribution_year", "last_period_year"),
]

# (model, old field name, new field name) for ir_model_fields
FIELD_RENAMES = [
    ("account.move.line", "membership_contribution_id", "membership_period_id"),
    (
        "res.company",
        "membership_default_contribution_year",
        "membership_default_period_year",
    ),
    ("membership.membership", "last_contribution_year", "last_period_year"),
    ("membership.membership", "contribution_ids", "period_ids"),
    ("membership.membership", "contribution_count", "period_count"),
    (
        "membership.membership",
        "duplicate_contribution_year_warning",
        "duplicate_period_year_warning",
    ),
    ("res.partner", "membership_contribution_ids", "membership_period_ids"),
    ("res.users", "membership_contribution_ids", "membership_period_ids"),
    ("donation.tax.receipt", "membership_contribution_ids", "membership_period_ids"),
]


def _table_exists(cr, table):
    cr.execute("SELECT to_regclass(%s)", (table,))
    return cr.fetchone()[0] is not None


def _column_exists(cr, table, column):
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
        WHERE table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return bool(cr.fetchone())


def migrate(cr, version):
    if not version:
        return

    for old, new in TABLE_RENAMES:
        if _table_exists(cr, old) and not _table_exists(cr, new):
            cr.execute('ALTER TABLE "%s" RENAME TO "%s"' % (old, new))
            _logger.info("renamed table %s to %s", old, new)
        # The sequence follows the table but keeps its own name.
        cr.execute("SELECT to_regclass(%s)", ("%s_id_seq" % old,))
        if cr.fetchone()[0] is not None:
            cr.execute(
                'ALTER SEQUENCE "%s_id_seq" RENAME TO "%s_id_seq"' % (old, new)
            )

    # The unique constraint carries the old table name. Drop it here and let
    # the ORM recreate it under the new one.
    cr.execute(
        """
        ALTER TABLE membership_period
        DROP CONSTRAINT IF EXISTS membership_contribution_membership_year_uniq
        """
    )
    cr.execute(
        """
        DELETE FROM ir_model_constraint
        WHERE name = 'membership_contribution_membership_year_uniq'
        """
    )

    for table, old, new in COLUMN_RENAMES:
        if _column_exists(cr, table, old) and not _column_exists(cr, table, new):
            cr.execute(
                'ALTER TABLE "%s" RENAME COLUMN "%s" TO "%s"' % (table, old, new)
            )
            _logger.info("renamed %s.%s to %s", table, old, new)

    # The ir_model row keeps its id, so ir.model.access and ir.rule stay valid.
    cr.execute(
        "UPDATE ir_model SET model = 'membership.period' WHERE model = 'membership.contribution'"
    )
    cr.execute(
        "UPDATE ir_model_fields SET model = 'membership.period' WHERE model = 'membership.contribution'"
    )
    cr.execute(
        "UPDATE ir_model_fields SET relation = 'membership.period' WHERE relation = 'membership.contribution'"
    )
    cr.execute(
        "UPDATE ir_model_data SET model = 'membership.period' WHERE model = 'membership.contribution'"
    )
    cr.execute(
        """
        UPDATE ir_model_data SET name = 'model_membership_period'
        WHERE module = 'association_membership' AND name = 'model_membership_contribution'
        """
    )

    for model, old, new in FIELD_RENAMES:
        cr.execute(
            "UPDATE ir_model_fields SET name = %s WHERE model = %s AND name = %s",
            (new, model, old),
        )

    # Views, actions, menus and ACLs are recreated from the new XML ids; the
    # module update removes the orphans left behind.
    _logger.info("membership.contribution renamed to membership.period")
