"""Connect the same-gender mode added in Prototype4.

Revision ID: c82e741bd509
Revises: 187dfc1e85e4
"""

import sqlalchemy as sa
from alembic import op

revision = "c82e741bd509"
down_revision = "187dfc1e85e4"
branch_labels = None
depends_on = None


def replace_checks(expression):
    for table, constraint in (
        ("session_templates", "ck_template_round_format"),
        ("tennis_sessions", "ck_session_round_format"),
    ):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="check")
            batch.create_check_constraint(constraint, expression)


def change_formats(expression):
    if op.get_context().as_sql:
        raise RuntimeError(
            "Run this migration online so existing foreign keys can be checked."
        )
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        replace_checks(expression)
        return

    # SQLite must rebuild the two parent tables to change their CHECKs.
    # Disable enforcement only on this migration connection, outside a
    # transaction; copy both tables atomically and check all FKs before commit.
    with op.get_context().autocommit_block():
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            connection.exec_driver_sql("BEGIN")
            replace_checks(expression)
            if connection.exec_driver_sql("PRAGMA foreign_key_check").first():
                raise RuntimeError(
                    "Foreign key validation failed; format migration rolled back."
                )
            connection.exec_driver_sql("COMMIT")
        except Exception:
            connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql(f"PRAGMA foreign_keys={int(foreign_keys)}")


def upgrade():
    change_formats("round_format IN ('open', 'mixed', 'same_gender')")


def downgrade():
    connection = op.get_bind()
    for table in ("session_templates", "tennis_sessions"):
        if connection.execute(
            sa.text(f"SELECT 1 FROM {table} WHERE round_format='same_gender' LIMIT 1")
        ).first():
            raise RuntimeError(
                "Same-gender sessions/templates must be backed up before downgrading."
            )
    change_formats("round_format IN ('open', 'mixed')")
