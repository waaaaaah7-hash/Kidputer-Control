"""passkeys and remembered devices

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('passkeys',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('credential_id', sa.LargeBinary(), nullable=False),
    sa.Column('public_key', sa.LargeBinary(), nullable=False),
    sa.Column('sign_count', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('transports', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('backed_up', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('credential_id')
    )
    op.create_index(op.f('ix_passkeys_user_id'), 'passkeys', ['user_id'], unique=False)
    op.create_table('remembered_devices',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.LargeBinary(length=32), nullable=False),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_remembered_devices_user_id'), 'remembered_devices', ['user_id'], unique=False)
    op.create_table('webauthn_challenges',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('challenge', sa.LargeBinary(), nullable=False),
    sa.Column('purpose', sa.String(length=16), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("purpose IN ('register', 'login', 'reverify')", name='ck_webauthn_challenge_purpose'),
    sa.ForeignKeyConstraint(['user_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_webauthn_challenges_expires_at'), 'webauthn_challenges', ['expires_at'], unique=False)
    op.add_column('admin_sessions', sa.Column('auth_method', sa.String(length=24), nullable=True))
    op.add_column('admin_sessions', sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('admin_sessions', sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('admin_sessions', sa.Column('remembered_device_id', sa.UUID(), nullable=True))
    op.create_foreign_key('fk_admin_sessions_remembered_device', 'admin_sessions', 'remembered_devices', ['remembered_device_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    op.drop_constraint('fk_admin_sessions_remembered_device', 'admin_sessions', type_='foreignkey')
    op.drop_column('admin_sessions', 'remembered_device_id')
    op.drop_column('admin_sessions', 'last_seen_at')
    op.drop_column('admin_sessions', 'verified_at')
    op.drop_column('admin_sessions', 'auth_method')
    op.drop_index(op.f('ix_webauthn_challenges_expires_at'), table_name='webauthn_challenges')
    op.drop_table('webauthn_challenges')
    op.drop_index(op.f('ix_remembered_devices_user_id'), table_name='remembered_devices')
    op.drop_table('remembered_devices')
    op.drop_index(op.f('ix_passkeys_user_id'), table_name='passkeys')
    op.drop_table('passkeys')
