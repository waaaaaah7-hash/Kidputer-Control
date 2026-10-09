"""parents names removal invites

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('admin_invites',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('token_hash', sa.LargeBinary(length=32), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['admin_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.add_column('admin_users', sa.Column('display_name', sa.String(length=60), nullable=True))
    op.add_column('admin_users', sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('admin_users', 'disabled_at')
    op.drop_column('admin_users', 'display_name')
    op.drop_table('admin_invites')
