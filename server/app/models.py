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
from datetime import datetime, time

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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

    __table_args__ = (Index("uq_admin_users_email_lower", func.lower(email), unique=True),)


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

    user: Mapped[AdminUser] = relationship()

    __table_args__ = (CheckConstraint("stage IN ('mfa_pending', 'active')", name="ck_admin_sessions_stage"),)


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
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    device: Mapped[Device] = relationship(back_populates="settings")

    __table_args__ = (
        CheckConstraint("countdown_seconds BETWEEN 0 AND 3600", name="ck_settings_countdown"),
        CheckConstraint("poll_interval_seconds BETWEEN 5 AND 3600", name="ck_settings_poll"),
        CheckConstraint("pin_max_attempts BETWEEN 1 AND 20", name="ck_settings_pin_attempts"),
        CheckConstraint("pin_override_minutes BETWEEN 1 AND 1440", name="ck_settings_pin_override"),
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
        # At most one live message per device, (a new message replaces the current one).
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
