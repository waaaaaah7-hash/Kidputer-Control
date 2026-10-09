"""Device mode: Managed (standard child account) or Trusted (administrator child account).

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("mode", sa.String(length=16), server_default=sa.text("'managed'"), nullable=False))
    op.create_check_constraint("ck_devices_mode", "devices", "mode IN ('managed', 'trusted')")


def downgrade() -> None:
    op.drop_constraint("ck_devices_mode", "devices", type_="check")
    op.drop_column("devices", "mode")
