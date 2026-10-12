"""Add pairing (variety) mode to sessions and templates

Revision ID: f4b2c8d1e6a7
Revises: e3a1f5c2b9d4
Create Date: 2026-10-12 18:00:00.000000

"""
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = 'f4b2c8d1e6a7'
down_revision = 'e3a1f5c2b9d4'
branch_labels = None
depends_on = None


def upgrade():
    # 1 = avoid repeats, 2 = balanced skill (what every existing session used), 3 = round robin
    with op.batch_alter_table('session_templates', schema=None) as batch_op:
        batch_op.add_column(sa.Column('variety_mode', sa.Integer(), server_default='2', nullable=False))
    with op.batch_alter_table('tennis_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('variety_mode', sa.Integer(), server_default='2', nullable=False))


def downgrade():
    # Native DROP COLUMN does not rebuild referenced tables (SQLite 3.35+).
    op.drop_column('tennis_sessions', 'variety_mode')
    op.drop_column('session_templates', 'variety_mode')
