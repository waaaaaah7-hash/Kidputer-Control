"""App rules, custom apps, and daily usage per app (roadmap #3, #4, #5).

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_rules',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('device_id', sa.UUID(), nullable=False),
    sa.Column('target_kind', sa.String(length=16), nullable=False),
    sa.Column('target', sa.String(length=80), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('windows', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("mode IN ('allow', 'block', 'schedule')", name='ck_app_rules_mode'),
    sa.CheckConstraint("target_kind IN ('app', 'category')", name='ck_app_rules_kind'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_id', 'target_kind', 'target', name='uq_app_rules_target')
    )
    op.create_table('custom_apps',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('matchers', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['admin_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('usage_daily',
    sa.Column('device_id', sa.UUID(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('app_key', sa.String(length=120), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=True),
    sa.Column('seconds', sa.Integer(), nullable=False),
    sa.Column('path', sa.String(length=1024), nullable=True),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('device_id', 'day', 'app_key')
    )


def downgrade() -> None:
    op.drop_table('usage_daily')
    op.drop_table('custom_apps')
    op.drop_table('app_rules')
