import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Annotated

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..agent_bundle import load_bundle
from ..audit import log_admin
from ..config import get_settings
from ..installer import build_installer_zip
from ..deps import AdminDep, DbDep, SettingsDep, VerifiedAdminDep, utcnow
from ..models import ActivityEvent, AdminUser, Device, DeviceSettings, LockRequest, Message, Override, ScheduleEntry
from ..schemas import (
    DeviceCreate,
    DeviceOut,
    DeviceUpdate,
    EnrollmentCodeOut,
    EventOut,
    InstallerRequest,
    LockIn,
    LockOut,
    MessageIn,
    MessageOut,
    OverrideIn,
    OverrideOut,
    PinIn,
    ScheduleEntryIn,
    ScheduleIn,
    SettingsIn,
    SettingsOut,
)
from ..security import hash_pin, new_enrollment_code, token_hash

router = APIRouter(tags=["admin"])


def _device(db: Session, device_id: uuid.UUID, lock: bool = False) -> Device:
    device = db.get(Device, device_id, with_for_update=lock)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return device


def _device_out(d: Device) -> DeviceOut:
    return DeviceOut(
        id=d.id,
        name=d.name,
        timezone=d.timezone,
        enrolled=d.enrolled_at is not None and d.token_hash is not None,
        hostname=d.hostname,
        agent_version=d.agent_version,
        last_seen_at=d.last_seen_at,
        last_status=d.last_status,
        created_at=d.created_at,
        mode=d.mode,
        agent_update_available=_update_available(d),
    )


def _update_available(d: Device) -> str | None:
    bundle = load_bundle(get_settings().agent_dir)
    running = (d.last_status or {}).get("agent_bundle")
    if bundle is None or not running or running == bundle.fingerprint:
        return None
    return bundle.version


def _settings_out(s: DeviceSettings) -> SettingsOut:
    return SettingsOut(
        enforcement_enabled=s.enforcement_enabled,
        countdown_seconds=s.countdown_seconds,
        poll_interval_seconds=s.poll_interval_seconds,
        pin_set=s.pin_hash is not None,
        pin_max_attempts=s.pin_max_attempts,
        pin_lockout_minutes=s.pin_lockout_minutes,
        pin_override_minutes=s.pin_override_minutes,
        silent_alert_minutes=s.silent_alert_minutes,
        show_window_titles=s.show_window_titles,
        auto_update=s.auto_update,
        updated_at=s.updated_at,
    )


def _issue_enrollment_code(device: Device, settings: SettingsDep) -> EnrollmentCodeOut:
    code = new_enrollment_code()
    device.enrollment_code_hash = token_hash(code)
    device.enrollment_expires_at = utcnow() + timedelta(hours=settings.enrollment_code_ttl_hours)
    return EnrollmentCodeOut(device_id=device.id, enrollment_code=code, expires_at=device.enrollment_expires_at)


# --- Devices -----------------------------------------------------------------------


@router.get("/devices", response_model=list[DeviceOut])
def list_devices(db: DbDep, admin: AdminDep) -> list[DeviceOut]:
    return [_device_out(d) for d in db.scalars(select(Device).order_by(Device.created_at))]


@router.post("/devices", response_model=EnrollmentCodeOut, status_code=status.HTTP_201_CREATED)
def create_device(body: DeviceCreate, db: DbDep, settings: SettingsDep, admin: AdminDep) -> EnrollmentCodeOut:
    device = Device(id=uuid.uuid4(), name=body.name, timezone=body.timezone, mode=body.mode)
    device.settings = DeviceSettings()
    db.add(device)
    out = _issue_enrollment_code(device, settings)
    db.flush()
    log_admin(db, admin, "device.created", f"Device '{device.name}' created", device.id)
    db.commit()
    return out


@router.get("/devices/{device_id}", response_model=DeviceOut)
def get_device(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> DeviceOut:
    return _device_out(_device(db, device_id))


@router.patch("/devices/{device_id}", response_model=DeviceOut)
def update_device(device_id: uuid.UUID, body: DeviceUpdate, db: DbDep, admin: AdminDep) -> DeviceOut:
    device = _device(db, device_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changes.items():
        setattr(device, key, value)
    log_admin(db, admin, "device.updated", f"Device '{device.name}' updated", device.id, changes)
    db.commit()
    return _device_out(device)


@router.delete("/devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(device_id: uuid.UUID, db: DbDep, admin: VerifiedAdminDep) -> None:
    device = _device(db, device_id)
    log_admin(db, admin, "device.deleted", f"Device '{device.name}' ({device.id}) deleted", None, {"device_id": str(device.id)})
    db.delete(device)
    db.commit()


@router.post("/devices/{device_id}/enrollment-code", response_model=EnrollmentCodeOut)
def reissue_enrollment_code(device_id: uuid.UUID, db: DbDep, settings: SettingsDep, admin: VerifiedAdminDep) -> EnrollmentCodeOut:
    """Issue a fresh enrollment code and revoke the current device token (use to reinstall or rotate)."""
    device = _device(db, device_id, lock=True)
    device.token_hash = None
    out = _issue_enrollment_code(device, settings)
    log_admin(db, admin, "device.token_revoked", f"Device '{device.name}' token revoked; new enrollment code issued", device.id)
    db.commit()
    return out


@router.post("/devices/{device_id}/installer", response_class=Response)
def download_installer(
    device_id: uuid.UUID, body: InstallerRequest, request: Request, db: DbDep, settings: SettingsDep, admin: VerifiedAdminDep
) -> Response:
    """One-click installer zip with a freshly issued enrollment code and this server's address baked in."""
    device = _device(db, device_id, lock=True)
    if device.token_hash is not None and not body.replace:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This PC is already installed. A new installer disconnects it until it's reinstalled; confirm to continue.",
        )
    api_url = (settings.public_url or str(request.base_url)).rstrip("/")
    if body.mode is not None:
        device.mode = body.mode
    device.token_hash = None
    code = _issue_enrollment_code(device, settings)
    try:
        data = build_installer_zip(
            Path(settings.agent_dir), api_url, code.enrollment_code, code.expires_at, device.name, body.child_user, device.mode
        )
    except FileNotFoundError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Installer files are not available on this server") from exc
    log_admin(db, admin, "device.installer_downloaded", f"Installer downloaded for '{device.name}'", device.id,
              {"child_user": body.child_user, "replaced_install": body.replace, "mode": device.mode})
    db.commit()
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="KidputerControl-Installer.zip"',
            # The dashboard shows the new code too, for installing by hand.
            "X-Enrollment-Code": code.enrollment_code,
            "X-Enrollment-Expires": code.expires_at.isoformat(),
        },
    )


# --- Schedule & settings -------------------------------------------------------------


@router.get("/devices/{device_id}/schedule", response_model=ScheduleIn)
def get_schedule(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> ScheduleIn:
    device = _device(db, device_id)
    return ScheduleIn(
        entries=[ScheduleEntryIn(day_of_week=e.day_of_week, lock_at=e.lock_at, unlock_at=e.unlock_at) for e in device.schedule]
    )


@router.put("/devices/{device_id}/schedule", response_model=ScheduleIn)
def put_schedule(device_id: uuid.UUID, body: ScheduleIn, db: DbDep, admin: AdminDep) -> ScheduleIn:
    device = _device(db, device_id, lock=True)
    db.execute(delete(ScheduleEntry).where(ScheduleEntry.device_id == device.id))
    db.add_all(
        ScheduleEntry(device_id=device.id, day_of_week=e.day_of_week, lock_at=e.lock_at, unlock_at=e.unlock_at)
        for e in body.entries
    )
    log_admin(db, admin, "config.schedule_changed", "Schedule updated", device.id, body.model_dump(mode="json"))
    db.commit()
    db.expire(device, ["schedule"])
    return body


@router.get("/devices/{device_id}/settings", response_model=SettingsOut)
def get_settings_(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> SettingsOut:
    return _settings_out(_device(db, device_id).settings)


@router.patch("/devices/{device_id}/settings", response_model=SettingsOut)
def patch_settings(device_id: uuid.UUID, body: SettingsIn, db: DbDep, admin: AdminDep) -> SettingsOut:
    device = _device(db, device_id, lock=True)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for key, value in changes.items():
        setattr(device.settings, key, value)
    device.settings.updated_at = utcnow()
    log_admin(db, admin, "config.settings_changed", "Settings updated", device.id, changes)
    db.commit()
    return _settings_out(device.settings)


@router.put("/devices/{device_id}/pin", status_code=status.HTTP_204_NO_CONTENT)
def set_pin(device_id: uuid.UUID, body: PinIn, db: DbDep, settings: SettingsDep, admin: VerifiedAdminDep) -> None:
    device = _device(db, device_id, lock=True)
    s = device.settings
    s.pin_hash, s.pin_salt = hash_pin(body.pin, settings.pin_pbkdf2_iterations)
    s.pin_iterations = settings.pin_pbkdf2_iterations
    s.updated_at = utcnow()
    log_admin(db, admin, "config.pin_changed", "Lock-screen PIN changed", device.id)
    db.commit()


@router.delete("/devices/{device_id}/pin", status_code=status.HTTP_204_NO_CONTENT)
def clear_pin(device_id: uuid.UUID, db: DbDep, admin: VerifiedAdminDep) -> None:
    device = _device(db, device_id, lock=True)
    s = device.settings
    s.pin_hash = s.pin_salt = s.pin_iterations = None
    s.updated_at = utcnow()
    log_admin(db, admin, "config.pin_cleared", "Lock-screen PIN removed", device.id)
    db.commit()


# --- Overrides ------------------------------------------------------------------------


@router.get("/devices/{device_id}/overrides", response_model=list[OverrideOut])
def list_overrides(
    device_id: uuid.UUID, db: DbDep, admin: AdminDep, include_past: bool = False
) -> list[Override]:
    _device(db, device_id)
    q = select(Override).where(Override.device_id == device_id)
    if not include_past:
        q = q.where(Override.cancelled_at.is_(None), Override.ends_at > utcnow())
    return list(db.scalars(q.order_by(Override.starts_at.desc()).limit(200)))


@router.post("/devices/{device_id}/overrides", response_model=OverrideOut, status_code=status.HTTP_201_CREATED)
def create_override(device_id: uuid.UUID, body: OverrideIn, db: DbDep, admin: AdminDep) -> Override:
    device = _device(db, device_id)
    starts = body.starts_at or utcnow()
    ends = body.ends_at or starts + timedelta(minutes=body.duration_minutes)
    if ends <= starts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "ends_at must be after starts_at")
    if ends <= utcnow():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "override would already be over")
    o = Override(device_id=device.id, starts_at=starts, ends_at=ends, reason=body.reason, created_by=admin.id)
    db.add(o)
    db.flush()
    local_end = ends.astimezone(ZoneInfo(device.timezone))
    log_admin(
        db, admin, "override.created", f"Unlocked until {local_end:%a %H:%M} ({device.timezone})", device.id,
        {"override_id": str(o.id), "starts_at": starts.isoformat(), "ends_at": ends.isoformat(), "reason": body.reason},
    )
    db.commit()
    return o


@router.delete("/devices/{device_id}/overrides/{override_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_override(device_id: uuid.UUID, override_id: uuid.UUID, db: DbDep, admin: AdminDep) -> None:
    o = db.get(Override, override_id)
    if o is None or o.device_id != device_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "override not found")
    if o.cancelled_at is None:
        o.cancelled_at = utcnow()
        log_admin(db, admin, "override.cancelled", "Override cancelled", device_id, {"override_id": str(o.id)})
        db.commit()


# --- Custom message ---------------------------------------------------------------------


def _active_message(db: Session, device_id: uuid.UUID) -> Message | None:
    return db.scalar(
        select(Message).where(Message.device_id == device_id, Message.cleared_at.is_(None), Message.dismissed_at.is_(None))
    )


@router.get("/devices/{device_id}/message", response_model=MessageOut | None)
def get_message(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> Message | None:
    _device(db, device_id)
    return _active_message(db, device_id)


@router.post("/devices/{device_id}/message", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
def send_message(device_id: uuid.UUID, body: MessageIn, db: DbDep, admin: AdminDep) -> Message:
    """Show a message on the child's PC, replacing any message currently shown."""
    device = _device(db, device_id, lock=True)
    now = utcnow()
    if (current := _active_message(db, device.id)) is not None:
        current.cleared_at = now
        db.flush()
    m = Message(device_id=device.id, body=body.body, mode=body.mode, created_by=admin.id)
    db.add(m)
    db.flush()
    log_admin(db, admin, "message.sent", f"{body.mode.capitalize()} message sent", device.id, {"message_id": str(m.id), "body": body.body})
    db.commit()
    return m


@router.delete("/devices/{device_id}/message", status_code=status.HTTP_204_NO_CONTENT)
def clear_message(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> None:
    _device(db, device_id, lock=True)
    if (current := _active_message(db, device_id)) is not None:
        current.cleared_at = utcnow()
        log_admin(db, admin, "message.cleared", "Message cleared by admin", device_id, {"message_id": str(current.id)})
        db.commit()


# --- Immediate lock -----------------------------------------------------------------------


def _active_lock(db: Session, device_id: uuid.UUID) -> LockRequest | None:
    return db.scalar(select(LockRequest).where(LockRequest.device_id == device_id, LockRequest.cleared_at.is_(None)))


@router.get("/devices/{device_id}/lock", response_model=LockOut | None)
def get_lock(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> LockRequest | None:
    _device(db, device_id)
    return _active_lock(db, device_id)


@router.post("/devices/{device_id}/lock", response_model=LockOut)
def request_lock(device_id: uuid.UUID, body: LockIn, db: DbDep, admin: AdminDep) -> LockRequest:
    """Lock now, ahead of any active override (which is kept, not cancelled). Idempotent."""
    device = _device(db, device_id, lock=True)
    if (existing := _active_lock(db, device.id)) is not None:
        return existing
    lock = LockRequest(device_id=device.id, reason=body.reason, created_by=admin.id)
    db.add(lock)
    db.flush()
    log_admin(db, admin, "lock.requested", "Immediate lock requested", device.id, {"lock_request_id": str(lock.id), "reason": body.reason})
    db.commit()
    return lock


@router.delete("/devices/{device_id}/lock", status_code=status.HTTP_204_NO_CONTENT)
def clear_lock(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> None:
    _device(db, device_id, lock=True)
    if (lock := _active_lock(db, device_id)) is not None:
        lock.cleared_at = utcnow()
        log_admin(db, admin, "lock.cleared", "Immediate lock cleared", device_id, {"lock_request_id": str(lock.id)})
        db.commit()


# --- Activity log -----------------------------------------------------------------------------


@router.get("/events", response_model=list[EventOut])
def list_events(
    db: DbDep,
    admin: AdminDep,
    device_id: uuid.UUID | None = None,
    event_type: str | None = Query(default=None, max_length=64),
    source: str | None = Query(default=None, pattern="^(agent|admin|system)$"),
    severity: str | None = Query(default=None, pattern="^(info|warning|critical)$"),
    since: datetime | None = None,
    until: datetime | None = None,
    before_id: Annotated[int | None, Query(description="Pagination cursor: return events with id < before_id")] = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[EventOut]:
    q = select(ActivityEvent)
    if device_id is not None:
        q = q.where(ActivityEvent.device_id == device_id)
    if event_type is not None:
        q = q.where(ActivityEvent.event_type == event_type)
    if source is not None:
        q = q.where(ActivityEvent.source == source)
    if severity is not None:
        q = q.where(ActivityEvent.severity == severity)
    if since is not None:
        q = q.where(ActivityEvent.occurred_at >= since)
    if until is not None:
        q = q.where(ActivityEvent.occurred_at < until)
    if before_id is not None:
        q = q.where(ActivityEvent.id < before_id)
    events = list(db.scalars(q.order_by(ActivityEvent.id.desc()).limit(limit)))
    actor_ids = {e.actor_user_id for e in events if e.actor_user_id}
    names = {u.id: u.name for u in db.scalars(select(AdminUser).where(AdminUser.id.in_(actor_ids)))} if actor_ids else {}
    out = []
    for e in events:
        item = EventOut.model_validate(e)
        item.actor_name = names.get(e.actor_user_id) if e.actor_user_id else None
        out.append(item)
    return out
