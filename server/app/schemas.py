import uuid
from datetime import datetime, time
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

# --- Auth ---------------------------------------------------------------------


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)


class LoginResponse(BaseModel):
    mfa_required: bool
    totp_setup_required: bool = False


class MfaRequest(BaseModel):
    code: str = Field(max_length=16)
    # "Remember this device": later password sign-ins on this browser skip the code.
    remember: bool = False


class TotpSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str


class AdminOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    totp_enabled: bool
    # Shown in the activity log; `name` falls back to the start of the email when no name is set.
    display_name: str | None
    name: str


# A parent's name as shown to the other parents; surrounding spaces don't count.
DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class ProfileUpdate(BaseModel):
    display_name: DisplayName


class ParentOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    totp_enabled: bool
    passkeys: int
    created_at: datetime
    last_active_at: datetime | None
    you: bool


class InviteCreate(BaseModel):
    email: EmailStr


class InviteOut(BaseModel):
    id: uuid.UUID
    email: str
    invited_by: str | None
    created_at: datetime
    expires_at: datetime


class InviteCreated(BaseModel):
    invite: InviteOut
    # Shown once: the secret part is after "#", so it never reaches the server or its logs when opened.
    link: str


class InviteToken(BaseModel):
    token: str = Field(min_length=10, max_length=100)


class InviteInfo(BaseModel):
    email: str
    invited_by: str | None
    expires_at: datetime


class InviteAccept(InviteToken):
    display_name: DisplayName
    password: str = Field(min_length=12, max_length=1024)


class SessionOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime | None
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    auth_method: str | None
    remembered: bool
    current: bool


class RememberedDeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_agent: str | None
    ip: str | None
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    current: bool = False


class PasskeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    backed_up: bool
    created_at: datetime
    last_used_at: datetime | None


class PasskeyRename(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PasskeyOptionsOut(BaseModel):
    challenge_id: uuid.UUID
    # PublicKeyCredentialCreationOptionsJSON / RequestOptionsJSON, passed straight to the browser.
    options: dict[str, Any]


class PasskeyVerifyRequest(BaseModel):
    challenge_id: uuid.UUID
    # The browser's credential as JSON (navigator.credentials.create/get, serialized).
    credential: dict[str, Any]
    name: str | None = Field(default=None, max_length=100)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


# --- Devices & configuration ---------------------------------------------------


def _check_tz(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone {value!r}") from exc
    return value


DeviceMode = Literal["managed", "trusted"]


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    timezone: str = "UTC"
    mode: DeviceMode = "managed"

    _tz = field_validator("timezone")(_check_tz)


class DeviceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    timezone: str | None = None
    mode: DeviceMode | None = None

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        return None if v is None else _check_tz(v)


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    timezone: str
    enrolled: bool
    hostname: str | None
    agent_version: str | None
    last_seen_at: datetime | None
    last_status: dict[str, Any] | None
    created_at: datetime
    mode: DeviceMode


class EnrollmentCodeOut(BaseModel):
    device_id: uuid.UUID
    enrollment_code: str
    expires_at: datetime


class InstallerRequest(BaseModel):
    # Windows account to enforce; blank = the installer asks on the PC.
    child_user: str | None = Field(default=None, max_length=104, pattern=r"^[^\x00-\x1f\"/:*?<>|]+$")
    # Required when the PC is already installed: a new code disconnects it until it's reinstalled.
    replace: bool = False
    # Also saved as the device's mode. Trusted lets the installer accept an administrator child account.
    mode: DeviceMode | None = None


class ScheduleEntryIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6, description="0 = Monday")
    lock_at: time
    unlock_at: time

    @model_validator(mode="after")
    def _nonempty(self) -> "ScheduleEntryIn":
        if self.lock_at == self.unlock_at:
            raise ValueError("lock_at and unlock_at must differ")
        return self


class ScheduleIn(BaseModel):
    entries: list[ScheduleEntryIn] = Field(max_length=7)

    @field_validator("entries")
    @classmethod
    def _unique_days(cls, v: list[ScheduleEntryIn]) -> list[ScheduleEntryIn]:
        days = [e.day_of_week for e in v]
        if len(days) != len(set(days)):
            raise ValueError("each day_of_week may appear at most once")
        return v


class SettingsIn(BaseModel):
    enforcement_enabled: bool | None = None
    countdown_seconds: int | None = Field(default=None, ge=0, le=3600)
    poll_interval_seconds: int | None = Field(default=None, ge=5, le=3600)
    pin_max_attempts: int | None = Field(default=None, ge=1, le=20)
    pin_lockout_minutes: int | None = Field(default=None, ge=1, le=1440)
    pin_override_minutes: int | None = Field(default=None, ge=1, le=1440)


class SettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    enforcement_enabled: bool
    countdown_seconds: int
    poll_interval_seconds: int
    pin_set: bool
    pin_max_attempts: int
    pin_lockout_minutes: int
    pin_override_minutes: int
    updated_at: datetime


class PinIn(BaseModel):
    pin: str = Field(pattern=r"^\d{4,10}$")


class OverrideIn(BaseModel):
    starts_at: AwareDatetime | None = None  # default: now
    ends_at: AwareDatetime | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=7 * 24 * 60)
    reason: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _one_end(self) -> "OverrideIn":
        if (self.ends_at is None) == (self.duration_minutes is None):
            raise ValueError("give exactly one of ends_at or duration_minutes")
        return self


class OverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    reason: str | None
    created_at: datetime
    cancelled_at: datetime | None


class MessageIn(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    mode: Literal["persistent", "closable"]


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    body: str
    mode: str
    created_at: datetime
    displayed_at: datetime | None
    dismissed_at: datetime | None
    cleared_at: datetime | None


class LockIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class LockOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: str | None
    created_at: datetime
    applied_at: datetime | None
    cleared_at: datetime | None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: uuid.UUID | None
    source: str
    event_type: str
    severity: str
    message: str | None
    data: dict[str, Any] | None
    occurred_at: datetime
    received_at: datetime
    actor_user_id: uuid.UUID | None
    actor_name: str | None = None


# --- Agent protocol -------------------------------------------------------------


class EnrollRequest(BaseModel):
    enrollment_code: str = Field(max_length=32)
    hostname: str | None = Field(default=None, max_length=255)
    agent_version: str | None = Field(default=None, max_length=32)


class EnrollResponse(BaseModel):
    device_id: uuid.UUID
    device_token: str


class AgentEventIn(BaseModel):
    client_event_id: uuid.UUID
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")
    severity: Literal["info", "warning", "critical"] = "info"
    message: str | None = Field(default=None, max_length=4000)
    data: dict[str, Any] | None = None
    occurred_at: AwareDatetime


class AgentEventsIn(BaseModel):
    events: list[AgentEventIn] = Field(max_length=500)


class AgentEventsOut(BaseModel):
    accepted: int
    duplicates: int


class HeartbeatIn(BaseModel):
    agent_version: str | None = Field(default=None, max_length=32)
    status: dict[str, Any] = Field(default_factory=dict)
