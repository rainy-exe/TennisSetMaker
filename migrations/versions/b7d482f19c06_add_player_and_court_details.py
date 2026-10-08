"""Persist the details already used by the management pages.

Revision ID: b7d482f19c06
Revises: 69cdc4853e47
"""

import sqlalchemy as sa
from alembic import op

revision = "b7d482f19c06"
down_revision = "69cdc4853e47"
branch_labels = None
depends_on = None


def upgrade():
    # ADD COLUMN preserves existing players and all generated/final history.
    # Keep the check on the new column so SQLite needs no parent-table rebuild.
    op.add_column("players", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column(
        "players",
        sa.Column(
            "court_preference",
            sa.String(20),
            sa.CheckConstraint(
                "court_preference IS NULL OR "
                "court_preference IN ('Clay', 'Hard', 'Grass', 'Synthetic')",
                name="ck_player_court_preference",
            ),
            nullable=True,
        ),
    )
    op.add_column("courts", sa.Column("cover", sa.String(120), nullable=True))


def downgrade():
    connection = op.get_bind()
    if (
        connection.execute(
            sa.text(
                "SELECT id FROM players WHERE notes IS NOT NULL "
                "OR court_preference IS NOT NULL LIMIT 1"
            )
        ).first()
        or connection.execute(
            sa.text("SELECT id FROM courts WHERE cover IS NOT NULL LIMIT 1")
        ).first()
    ):
        raise RuntimeError("Back up player/court details before downgrading.")
    with op.batch_alter_table("players") as batch:
        batch.drop_constraint("ck_player_court_preference", type_="check")
        batch.drop_column("court_preference")
        batch.drop_column("notes")
    op.drop_column("courts", "cover")
