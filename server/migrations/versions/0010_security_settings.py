"""Per-PC settings for the Security tab: Windows updates, restarts at bedtime, weekly full scans (roadmap #23).

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('device_settings', sa.Column('updates_mode', sa.String(length=16), server_default=sa.text("'install'"), nullable=False))
    op.add_column('device_settings', sa.Column('feature_updates', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('device_settings', sa.Column('restart_for_updates', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('device_settings', sa.Column('weekly_full_scan', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.create_check_constraint('ck_settings_updates_mode', 'device_settings', "updates_mode IN ('install', 'report')")


def downgrade() -> None:
    op.drop_constraint('ck_settings_updates_mode', 'device_settings', type_='check')
    op.drop_column('device_settings', 'weekly_full_scan')
    op.drop_column('device_settings', 'restart_for_updates')
    op.drop_column('device_settings', 'feature_updates')
    op.drop_column('device_settings', 'updates_mode')
