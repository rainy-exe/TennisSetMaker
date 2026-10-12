"""Add account email fields to users (password reset, verification)

Revision ID: e3a1f5c2b9d4
Revises: d947ef8621ab
Create Date: 2026-10-12 12:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'e3a1f5c2b9d4'
down_revision = 'd947ef8621ab'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('session_version', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('email_verified_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('last_account_email_at', sa.DateTime(), nullable=True))


def downgrade():
    # Native DROP COLUMN does not rebuild a referenced users table (SQLite 3.35+).
    op.drop_column('users', 'last_account_email_at')
    op.drop_column('users', 'email_verified_at')
    op.drop_column('users', 'session_version')
