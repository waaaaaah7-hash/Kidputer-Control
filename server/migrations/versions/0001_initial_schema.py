"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('admin_users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('totp_secret', sa.String(length=64), nullable=True),
    sa.Column('totp_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('totp_last_counter', sa.BigInteger(), nullable=True),
    sa.Column('failed_logins', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_admin_users_email_lower', 'admin_users', [sa.literal_column('lower(email)')], unique=True)
    op.create_table('devices',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('timezone', sa.String(length=64), server_default=sa.text("'UTC'"), nullable=False),
    sa.Column('token_hash', sa.LargeBinary(length=32), nullable=True),
    sa.Column('enrollment_code_hash', sa.LargeBinary(length=32), nullable=True),
    sa.Column('enrollment_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('enrolled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('hostname', sa.String(length=255), nullable=True),
    sa.Column('agent_version', sa.String(length=32), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_ip', sa.String(length=64), nullable=True),
    sa.Column('last_status', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('enrollment_code_hash'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_table('activity_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('device_id', sa.UUID(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('severity', sa.String(length=16), server_default=sa.text("'info'"), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('client_event_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("severity IN ('info', 'warning', 'critical')", name='ck_events_severity'),
    sa.CheckConstraint("source IN ('agent', 'admin', 'system')", name='ck_events_source'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['admin_users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('device_id', 'client_event_id', name='uq_events_client_id')
    )
    op.create_index('ix_events_device_time', 'activity_events', ['device_id', 'occurred_at'], unique=False)
    op.create_index('ix_events_type_time', 'activity_events', ['event_type', 'occurred_at'], unique=False)
    op.create_table('admin_sessions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.LargeBinary(length=32), nullable=False),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.CheckConstraint("stage IN ('mfa_pending', 'active')", name='ck_admin_sessions_stage'),
    sa.ForeignKeyConstraint(['user_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_admin_sessions_user_id'), 'admin_sessions', ['user_id'], unique=False)
    op.create_table('device_settings',
    sa.Column('device_id', sa.UUID(), nullable=False),
    sa.Column('enforcement_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('countdown_seconds', sa.Integer(), server_default=sa.text('300'), nullable=False),
    sa.Column('poll_interval_seconds', sa.Integer(), server_default=sa.text('30'), nullable=False),
    sa.Column('pin_hash', sa.LargeBinary(length=32), nullable=True),
    sa.Column('pin_salt', sa.LargeBinary(length=16), nullable=True),
    sa.Column('pin_iterations', sa.Integer(), nullable=True),
    sa.Column('pin_max_attempts', sa.Integer(), server_default=sa.text('5'), nullable=False),
    sa.Column('pin_lockout_minutes', sa.Integer(), server_default=sa.text('15'), nullable=False),
    sa.Column('pin_override_minutes', sa.Integer(), server_default=sa.text('60'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('countdown_seconds BETWEEN 0 AND 3600', name='ck_settings_countdown'),
    sa.CheckConstraint('pin_max_attempts BETWEEN 1 AND 20', name='ck_settings_pin_attempts'),
    sa.CheckConstraint('pin_override_minutes BETWEEN 1 AND 1440', name='ck_settings_pin_override'),
    sa.CheckConstraint('poll_interval_seconds BETWEEN 5 AND 3600', name='ck_settings_poll'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('device_id')
    )
    op.create_table('lock_requests',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('device_id', sa.UUID(), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('applied_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cleared_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['admin_users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_lock_requests_one_active', 'lock_requests', ['device_id'], unique=True, postgresql_where=sa.text('cleared_at IS NULL'))
    op.create_table('messages',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('device_id', sa.UUID(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('displayed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cleared_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("mode IN ('persistent', 'closable')", name='ck_messages_mode'),
    sa.CheckConstraint('char_length(body) BETWEEN 1 AND 2000', name='ck_messages_body'),
    sa.ForeignKeyConstraint(['created_by'], ['admin_users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_messages_one_active', 'messages', ['device_id'], unique=True, postgresql_where=sa.text('cleared_at IS NULL AND dismissed_at IS NULL'))
    op.create_table('overrides',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('device_id', sa.UUID(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('ends_at > starts_at', name='ck_overrides_window'),
    sa.ForeignKeyConstraint(['created_by'], ['admin_users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_overrides_device_ends', 'overrides', ['device_id', 'ends_at'], unique=False)
    op.create_table('schedule_entries',
    sa.Column('device_id', sa.UUID(), nullable=False),
    sa.Column('day_of_week', sa.SmallInteger(), nullable=False),
    sa.Column('lock_at', sa.Time(), nullable=False),
    sa.Column('unlock_at', sa.Time(), nullable=False),
    sa.CheckConstraint('day_of_week BETWEEN 0 AND 6', name='ck_schedule_dow'),
    sa.CheckConstraint('lock_at <> unlock_at', name='ck_schedule_nonempty'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('device_id', 'day_of_week')
    )


def downgrade() -> None:
    op.drop_table('schedule_entries')
    op.drop_index('ix_overrides_device_ends', table_name='overrides')
    op.drop_table('overrides')
    op.drop_index('uq_messages_one_active', table_name='messages', postgresql_where=sa.text('cleared_at IS NULL AND dismissed_at IS NULL'))
    op.drop_table('messages')
    op.drop_index('uq_lock_requests_one_active', table_name='lock_requests', postgresql_where=sa.text('cleared_at IS NULL'))
    op.drop_table('lock_requests')
    op.drop_table('device_settings')
    op.drop_index(op.f('ix_admin_sessions_user_id'), table_name='admin_sessions')
    op.drop_table('admin_sessions')
    op.drop_index('ix_events_type_time', table_name='activity_events')
    op.drop_index('ix_events_device_time', table_name='activity_events')
    op.drop_table('activity_events')
    op.drop_table('devices')
    op.drop_index('uq_admin_users_email_lower', table_name='admin_users')
    op.drop_table('admin_users')
