"""Per-PC setting: how often to scan drives for folder sizes (roadmap #19).

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('device_settings', sa.Column('scan_every_days', sa.Integer(), server_default=sa.text('7'), nullable=False))
    op.create_check_constraint('ck_settings_scan_every', 'device_settings', 'scan_every_days BETWEEN 0 AND 90')


def downgrade() -> None:
    op.drop_constraint('ck_settings_scan_every', 'device_settings', type_='check')
    op.drop_column('device_settings', 'scan_every_days')
