"""Parent notifications: settings, push subscriptions, the notification queue, and "PC went silent".

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_secrets',
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_table('notification_settings',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('email_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('push_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('levels', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('quiet_start', sa.Time(), nullable=True),
    sa.Column('quiet_end', sa.Time(), nullable=True),
    sa.Column('timezone', sa.String(length=64), server_default=sa.text("'UTC'"), nullable=False),
    sa.Column('digest_hour', sa.SmallInteger(), server_default=sa.text('18'), nullable=False),
    sa.Column('last_digest_on', sa.Date(), nullable=True),
    sa.CheckConstraint('digest_hour BETWEEN 0 AND 23', name='ck_notification_digest_hour'),
    sa.ForeignKeyConstraint(['user_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table('push_subscriptions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('p256dh', sa.String(length=200), nullable=False),
    sa.Column('auth', sa.String(length=100), nullable=False),
    sa.Column('user_agent', sa.String(length=512), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('endpoint')
    )
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('device_id', sa.UUID(), nullable=True),
    sa.Column('event_id', sa.BigInteger(), nullable=True),
    sa.Column('category', sa.String(length=32), nullable=False),
    sa.Column('level', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('url', sa.String(length=300), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("level IN ('immediate', 'digest')", name='ck_notifications_level'),
    sa.ForeignKeyConstraint(['device_id'], ['devices.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['event_id'], ['activity_events.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['admin_users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_notifications_unsent', 'notifications', ['user_id', 'level'], unique=False, postgresql_where=sa.text('sent_at IS NULL'))
    op.create_index('ix_notifications_user_time', 'notifications', ['user_id', 'created_at'], unique=False)
    op.add_column('device_settings', sa.Column('silent_alert_minutes', sa.Integer(), server_default=sa.text('15'), nullable=False))
    op.create_check_constraint('ck_settings_silent_alert', 'device_settings', 'silent_alert_minutes BETWEEN 0 AND 1440')
    op.add_column('devices', sa.Column('silent_since', sa.DateTime(timezone=True), nullable=True))
    op.add_column('devices', sa.Column('last_silence_started', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('devices', 'last_silence_started')
    op.drop_column('devices', 'silent_since')
    op.drop_constraint('ck_settings_silent_alert', 'device_settings', type_='check')
    op.drop_column('device_settings', 'silent_alert_minutes')
    op.drop_index('ix_notifications_user_time', table_name='notifications')
    op.drop_index('ix_notifications_unsent', table_name='notifications', postgresql_where=sa.text('sent_at IS NULL'))
    op.drop_table('notifications')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
    op.drop_table('notification_settings')
    op.drop_table('app_secrets')
