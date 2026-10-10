"""Give existing clubs a permanent QR token without rotating shared links.

Revision ID: d947ef8621ab
Revises: c82e741bd509
"""

import uuid

import sqlalchemy as sa
from alembic import op

revision = "d947ef8621ab"
down_revision = "c82e741bd509"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    clubs = sa.table(
        "clubs",
        sa.column("id", sa.String(36)),
        sa.column("public_token", sa.String(64)),
    )
    ids = (
        connection.execute(sa.select(clubs.c.id).where(clubs.c.public_token.is_(None)))
        .scalars()
        .all()
    )
    for club_id in ids:
        connection.execute(
            clubs.update()
            .where(clubs.c.id == club_id)
            .values(public_token=uuid.uuid4().hex)
        )


def downgrade():
    # This is a data repair. Do not invalidate QR links on a data-only downgrade.
    pass
