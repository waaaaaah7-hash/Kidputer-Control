"""Postgres schema.

Maps the product's configuration concepts onto relational tables:
  schedule        -> schedule_entries (one row per weekday)
  PIN             -> device_settings.pin_*
  overrides       -> overrides
  custom message  -> messages
  immediate lock  -> lock_requests
  activity log    -> activity_events (central log for agent + admin actions)
"""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    totp_secret: Mapped[str | None] = mapped_column(String(64))
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Last accepted TOTP time-step, to reject replay of a code within its window.
    totp_last_counter: Mapped[int | None] = mapped_column(BigInteger)
    failed_logins: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    # Shown in the activity log ("Mum", "Dad"); falls back to the start of the email address.
    display_name: Mapped[str | None] = mapped_column(String(60))
    # A removed parent: kept (not deleted) so the activity log still says who did what.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("uq_admin_users_email_lower", func.lower(email), unique=True),)

    @property
    def name(self) -> str:
        return self.display_name or self.email.split("@", 1)[0]


class AdminInvite(Base):
    """A one-time link that lets another parent create their own account."""

    __tablename__ = "admin_invites"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    # 'mfa_pending' sessions only allow completing the TOTP step; 'active' sessions are fully authenticated.
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    # How this session was signed in: 'password_totp', 'passkey', 'password_remembered' or 'password'.
    auth_method: Mapped[str | None] = mapped_column(String(24))
    # Last time the parent proved it's them with a second factor (TOTP code or passkey). Risky actions
    # require this to be recent; a remembered-device sign-in starts without it.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remembered_device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("remembered_devices.id", ondelete="SET NULL"))

    user: Mapped[AdminUser] = relationship()

    __table_args__ = (CheckConstraint("stage IN ('mfa_pending', 'active')", name="ck_admin_sessions_stage"),)


class Passkey(Base):
    """A WebAuthn credential (passkey) the parent can sign in with instead of password + TOTP."""

    __tablename__ = "passkeys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    transports: Mapped[list | None] = mapped_column(JSONB)
    # True for passkeys that sync between devices (iCloud Keychain, Google Password Manager, ...).
    backed_up: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RememberedDevice(Base):
    """A browser the parent ticked "Remember this device" on: password sign-ins there skip the TOTP code."""

    __tablename__ = "remembered_devices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, unique=True)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WebAuthnChallenge(Base):
    """One-time challenge for a passkey ceremony; deleted when used, expires after a few minutes."""

    __tablename__ = "webauthn_challenges"

    id: Mapped[uuid.UUID] = _uuid_pk()
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # 'register', 'login' or 'reverify'
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    # Set for 'register' and 'reverify' (the signed-in parent); NULL for 'login'.
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)

    __table_args__ = (CheckConstraint("purpose IN ('register', 'login', 'reverify')", name="ck_webauthn_challenge_purpose"),)


class Device(Base):
    """A child PC running the agent."""

    __tablename__ = "devices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # IANA zone the schedule is expressed in (the agent evaluates the schedule on its own clock).
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    token_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    enrollment_code_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32), unique=True)
    enrollment_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hostname: Mapped[str | None] = mapped_column(String(255))
    agent_version: Mapped[str | None] = mapped_column(String(32))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ip: Mapped[str | None] = mapped_column(String(64))
    # Most recent status reported by the agent's heartbeat (locked, tamper state, ...).
    last_status: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created_at()
    # 'managed': the child has a standard Windows account and can't switch enforcement off.
    # 'trusted': the child is an administrator; enforcement works day to day, but the safety net is
    # notifications (installs, "PC went silent").
    mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'managed'"))
    # "PC went silent": set when the PC stops checking in while expected online; cleared on contact.
    silent_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Start of the most recent silence, kept after contact resumes so the agent's restart report can
    # tell "the PC was off" from "Kidputer Control was stopped while Windows was running".
    last_silence_started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # While a parent has a live view open (running apps, files), the PC checks in every few seconds.
    live_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("mode IN ('managed', 'trusted')", name="ck_devices_mode"),)

    settings: Mapped["DeviceSettings"] = relationship(back_populates="device", uselist=False, cascade="all, delete-orphan")
    schedule: Mapped[list["ScheduleEntry"]] = relationship(
        back_populates="device", cascade="all, delete-orphan", order_by="ScheduleEntry.day_of_week"
    )


class DeviceSettings(Base):
    __tablename__ = "device_settings"

    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    enforcement_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    countdown_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("300"))
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("30"))
    # Lock-screen PIN, verified offline by the agent. NULL = no PIN set (override only from the dashboard).
    pin_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    pin_salt: Mapped[bytes | None] = mapped_column(LargeBinary(16))
    pin_iterations: Mapped[int | None] = mapped_column(Integer)
    pin_max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    pin_lockout_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("15"))
    # Minutes of access a correct lock-screen PIN grants.
    pin_override_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("60"))
    # Alert parents when the PC hasn't checked in for this long. 0 = off.
    silent_alert_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("15"))
    # Window titles can reveal browser tabs or document names, so the PC only reports them if asked.
    show_window_titles: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Install new agent versions automatically when the server has them.
    auto_update: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    device: Mapped[Device] = relationship(back_populates="settings")

    __table_args__ = (
        CheckConstraint("countdown_seconds BETWEEN 0 AND 3600", name="ck_settings_countdown"),
        CheckConstraint("poll_interval_seconds BETWEEN 5 AND 3600", name="ck_settings_poll"),
        CheckConstraint("pin_max_attempts BETWEEN 1 AND 20", name="ck_settings_pin_attempts"),
        CheckConstraint("pin_override_minutes BETWEEN 1 AND 1440", name="ck_settings_pin_override"),
        CheckConstraint("silent_alert_minutes BETWEEN 0 AND 1440", name="ck_settings_silent_alert"),
    )


class ScheduleEntry(Base):
    """Nightly lock window for one weekday.

    The PC locks at lock_at on day_of_week and unlocks at unlock_at; if unlock_at <= lock_at the
    window runs past midnight into the next day. No row for a weekday = no lock that night.
    """

    __tablename__ = "schedule_entries"

    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, primary_key=True)  # 0 = Monday (ISO - 1)
    lock_at: Mapped[time] = mapped_column(Time, nullable=False)
    unlock_at: Mapped[time] = mapped_column(Time, nullable=False)

    device: Mapped[Device] = relationship(back_populates="schedule")

    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 0 AND 6", name="ck_schedule_dow"),
        CheckConstraint("lock_at <> unlock_at", name="ck_schedule_nonempty"),
    )


class Override(Base):
    """Admin-granted unlock window. Persistent: lives in the DB and the agent's cache, not in memory."""

    __tablename__ = "overrides"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_overrides_window"),
        Index("ix_overrides_device_ends", "device_id", "ends_at"),
    )


class Message(Base):
    """Custom on-screen message. 'persistent' = only admin can clear; 'closable' = child can dismiss."""

    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()
    displayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # by the child (closable only)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # by an admin

    __table_args__ = (
        CheckConstraint("mode IN ('persistent', 'closable')", name="ck_messages_mode"),
        CheckConstraint("char_length(body) BETWEEN 1 AND 2000", name="ck_messages_body"),
        # At most one live message per device: a new message replaces the current one.
        Index(
            "uq_messages_one_active",
            "device_id",
            unique=True,
            postgresql_where=text("cleared_at IS NULL AND dismissed_at IS NULL"),
        ),
    )


class LockRequest(Base):
    """Immediate lock. Takes precedence over an active override without cancelling it; stays in force until cleared."""

    __tablename__ = "lock_requests"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # agent acknowledged
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("uq_lock_requests_one_active", "device_id", unique=True, postgresql_where=text("cleared_at IS NULL")),
    )


class ActivityEvent(Base):
    """Central activity log: agent-reported events and admin actions."""

    __tablename__ = "activity_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'info'"))
    message: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONB)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    # Agent-generated id; lets the agent retry an upload of its offline queue without duplicating rows.
    client_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint("source IN ('agent', 'admin', 'system')", name="ck_events_source"),
        CheckConstraint("severity IN ('info', 'warning', 'critical')", name="ck_events_severity"),
        UniqueConstraint("device_id", "client_event_id", name="uq_events_client_id"),
        Index("ix_events_device_time", "device_id", "occurred_at"),
        Index("ix_events_type_time", "event_type", "occurred_at"),
    )


class NotificationSettings(Base):
    """How and when one parent wants to hear about things."""

    __tablename__ = "notification_settings"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), primary_key=True)
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    push_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    # {category: "immediate" | "digest" | "off"}; categories not listed use their default.
    levels: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    # Immediate alerts wait until quiet hours end. Both NULL = no quiet hours.
    quiet_start: Mapped[time | None] = mapped_column(Time)
    quiet_end: Mapped[time | None] = mapped_column(Time)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'UTC'"))
    digest_hour: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("18"))
    last_digest_on: Mapped[date | None] = mapped_column(Date)

    __table_args__ = (CheckConstraint("digest_hour BETWEEN 0 AND 23", name="ck_notification_digest_hour"),)


class PushSubscription(Base):
    """A browser or installed phone app that receives push notifications (Web Push)."""

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = _created_at()
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Notification(Base):
    """One thing to tell one parent about. Sent alone or grouped (immediate), or in the daily digest."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False)
    device_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("activity_events.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created_at()
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("level IN ('immediate', 'digest')", name="ck_notifications_level"),
        Index("ix_notifications_unsent", "user_id", "level", postgresql_where=text("sent_at IS NULL")),
        Index("ix_notifications_user_time", "user_id", "created_at"),
    )


class AppSecret(Base):
    """Server-generated secrets that must survive restarts (the Web Push signing key)."""

    __tablename__ = "app_secrets"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class DeviceCommand(Base):
    """Something a parent asked the PC to do (close an app, list a folder, ...). Delivered in the state
    snapshot; the agent reports the result."""

    __tablename__ = "device_commands"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'done', 'failed')", name="ck_device_commands_status"),
        Index("ix_device_commands_pending", "device_id", postgresql_where=text("status = 'pending'")),
    )


class DeviceReport(Base):
    """The latest copy of something the PC reports in bulk: running processes, drives, scans, ..."""

    __tablename__ = "device_reports"

    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class CustomApp(Base):
    """An app the parents added themselves (roadmap #4), shared by every PC in the household."""

    __tablename__ = "custom_apps"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # One of the catalogue's categories, or the parents' own ("Homework apps").
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    # Same shape as the catalogue's matchers: [{"exe": "...", "product": "..."}, ...]
    matchers: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = _created_at()


class AppRule(Base):
    """When an app or a whole category may be used on one PC (roadmap #3)."""

    __tablename__ = "app_rules"

    id: Mapped[uuid.UUID] = _uuid_pk()
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    # 'app' (a catalogue id or "custom:<uuid>") or 'category'
    target_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[str] = mapped_column(String(80), nullable=False)
    # 'allow' (any time), 'block' (never), 'schedule' (only inside `windows`)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    # [{"day": 0-6 (Monday = 0), "start": "16:00", "end": "19:00"}] in the PC's time zone
    windows: Mapped[list] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at: Mapped[datetime] = _created_at()

    __table_args__ = (
        CheckConstraint("target_kind IN ('app', 'category')", name="ck_app_rules_kind"),
        CheckConstraint("mode IN ('allow', 'block', 'schedule')", name="ck_app_rules_mode"),
        UniqueConstraint("device_id", "target_kind", "target", name="uq_app_rules_target"),
    )


class UsageDaily(Base):
    """Seconds an app was in front (and the child not idle) on one day, in the PC's time zone (#5)."""

    __tablename__ = "usage_daily"

    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    # A catalogue/custom app id, or "exe:<file name>" for anything else; "_total" = all screen time.
    app_key: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str | None] = mapped_column(String(60))
    seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    path: Mapped[str | None] = mapped_column(String(1024))
