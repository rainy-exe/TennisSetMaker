"""Add permanent club QR token

Revision ID: 187dfc1e85e4
Revises: 5b8fefaec99e
Create Date: 2026-10-10 12:08:54.951774

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '187dfc1e85e4'
down_revision = '5b8fefaec99e'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "clubs",
        sa.Column("public_token", sa.String(length=64), nullable=True)
    )

    op.create_index(
        "uq_clubs_public_token",
        "clubs",
        ["public_token"],
        unique=True
    )


def downgrade():
    op.drop_index(
        "uq_clubs_public_token",
        table_name="clubs"
    )

    op.drop_column("clubs", "public_token")