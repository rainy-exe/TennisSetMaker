"""Free the emails of removed team members

Removed members keep their row (rounds they edited still point at it), but
their email moves to removed_email and email becomes a placeholder, so the
address can register a new club or accept another invitation.

Revision ID: a7d3e9b5c1f8
Revises: f4b2c8d1e6a7
Create Date: 2026-10-12 15:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'a7d3e9b5c1f8'
down_revision = 'f4b2c8d1e6a7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('removed_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('removed_email', sa.String(length=255), nullable=True))

    # Members removed before this change were only disabled; free them too.
    op.execute(
        sa.text(
            "UPDATE users SET removed_email = email, "
            "email = 'removed+' || id || '@removed.invalid', "
            "removed_at = CURRENT_TIMESTAMP, "
            "session_version = session_version + 1 "
            "WHERE active = :inactive AND removed_at IS NULL"
        ).bindparams(inactive=False)
    )


def downgrade():
    # Removed members keep their placeholder email: the same address may have
    # been removed more than once or have a new account, so it can't safely
    # be put back. They were inactive either way.
    op.drop_column('users', 'removed_email')
    op.drop_column('users', 'removed_at')
