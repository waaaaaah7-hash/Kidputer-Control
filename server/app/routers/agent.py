"""Endpoints called by the SYSTEM watchdog on the child's PC.

Sync model: the agent polls GET /agent/state (conditional on ETag), caches the snapshot locally and
enforces from that cache — so enforcement keeps working offline. Everything it observes goes into a
local queue that is flushed to POST /agent/events; client_event_id makes retries idempotent.
"""

import json
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..agent_bundle import load_bundle
from ..audit import log_admin, log_system
from ..commands import save_report
from ..deps import DbDep, DeviceDep, SettingsDep, utcnow
from ..models import ActivityEvent, Device, DeviceCommand, LockRequest, Message, Override
from ..notify import queue_for_event
from ..schemas import AgentEventIn, AgentEventsIn, AgentEventsOut, CommandResultIn, EnrollRequest, EnrollResponse, HeartbeatIn
from ..security import new_token, normalize_enrollment_code, token_hash
from ..state import build_state, state_etag
from .app_rules import save_usage_report

router = APIRouter(prefix="/agent", tags=["agent"])


def _touch(db: Session, device: Device, request: Request) -> None:
    now = utcnow()
    device.last_seen_at = now
    device.last_ip = request.client.host if request.client else None
    if device.silent_since is not None:
        # Back after a silence. The agent's next "started" or "resumed" event says why it was quiet.
        gap = now - device.silent_since
        log_system(db, "device.back_online", f"{device.name} is back online after {_duration(gap)}.", device,
                   {"silent_since": device.silent_since.isoformat()})
        device.last_silence_started = device.silent_since
        device.silent_since = None


def _duration(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min" if minutes else f"{hours} h"
    return f"{hours // 24} days"


@router.post("/enroll", response_model=EnrollResponse)
def enroll(body: EnrollRequest, request: Request, db: DbDep) -> EnrollResponse:
    """Exchange a one-time enrollment code (shown in the dashboard) for a long-lived device token."""
    code_hash = token_hash(normalize_enrollment_code(body.enrollment_code))
    device = db.scalar(select(Device).where(Device.enrollment_code_hash == code_hash).with_for_update())
    if device is None or device.enrollment_expires_at is None or device.enrollment_expires_at <= utcnow():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired enrollment code")

    token = new_token()
    device.token_hash = token_hash(token)
    device.enrollment_code_hash = None
    device.enrollment_expires_at = None
    device.enrolled_at = utcnow()
    device.hostname = body.hostname
    device.agent_version = body.agent_version
    _touch(db, device, request)
    log_admin(db, None, "device.enrolled", f"Agent enrolled from {body.hostname or 'unknown host'}", device.id,
              {"hostname": body.hostname, "agent_version": body.agent_version})
    db.commit()
    return EnrollResponse(device_id=device.id, device_token=token)


@router.get("/state")
def get_state(
    request: Request,
    response: Response,
    db: DbDep,
    device: DeviceDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Any:
    now = utcnow()
    state = build_state(db, device, now)
    etag = state_etag(state)
    _touch(db, device, request)
    db.commit()
    headers = {"ETag": etag, "X-Server-Time": now.isoformat()}
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return {"etag": etag, "server_time": now.isoformat(), **state}


def _parse_uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


# Longest window a lock-screen PIN may grant (device_settings.pin_override_minutes is capped at a day).
_MAX_PIN_OVERRIDE = timedelta(days=1)


def _apply_side_effects(db: Session, device: Device, ev: AgentEventIn) -> None:
    """Fold agent acknowledgements back into the admin-visible state."""
    data = ev.data or {}
    if ev.event_type in ("message.displayed", "message.dismissed"):
        msg_id = _parse_uuid(data.get("message_id"))
        msg = db.get(Message, msg_id) if msg_id else None
        if msg is None or msg.device_id != device.id:
            return
        if ev.event_type == "message.displayed" and msg.displayed_at is None:
            msg.displayed_at = ev.occurred_at
        # A persistent message can only be cleared by an admin; the event is still logged.
        elif ev.event_type == "message.dismissed" and msg.mode == "closable" and msg.dismissed_at is None:
            msg.dismissed_at = ev.occurred_at
    elif ev.event_type == "lock.applied":
        lock_id = _parse_uuid(data.get("lock_request_id"))
        lock = db.get(LockRequest, lock_id) if lock_id else None
        if lock is not None and lock.device_id == device.id and lock.applied_at is None:
            lock.applied_at = ev.occurred_at
    elif ev.event_type == "lock.released_by_pin":
        # A parent entered the PIN at the lock screen: the immediate lock is over for everyone.
        lock_id = _parse_uuid(data.get("lock_request_id"))
        lock = db.get(LockRequest, lock_id) if lock_id else None
        if lock is not None and lock.device_id == device.id and lock.cleared_at is None:
            lock.cleared_at = ev.occurred_at
    elif ev.event_type == "agent.started":
        _check_restart_after_silence(db, device, ev)
    elif ev.event_type == "pc.resumed":
        device.last_silence_started = None  # it was asleep, with the watchdog still running
    elif ev.event_type == "pin.override_granted":
        # Mirror the agent's local PIN override so the dashboard shows it and the next sync keeps it.
        starts, ends = _parse_dt(data.get("starts_at")), _parse_dt(data.get("ends_at"))
        if starts is None or ends is None or not (starts < ends <= starts + _MAX_PIN_OVERRIDE):
            return
        db.add(Override(device_id=device.id, starts_at=starts, ends_at=ends, reason="Lock-screen PIN"))


def _check_restart_after_silence(db: Session, device: Device, ev: AgentEventIn) -> None:
    """The watchdog restarted after the PC was silent. If Windows had been running all along (booted
    before the silence began), Kidputer Control itself was stopped: tell the parents."""
    silence = device.last_silence_started
    if silence is None or ev.occurred_at < silence:
        return
    device.last_silence_started = None
    boot = _parse_dt((ev.data or {}).get("boot_time"))
    if boot is None or boot > silence - timedelta(minutes=2):
        return  # the PC was off (or restarted) during the silence: nothing suspicious
    log_system(
        db,
        "device.agent_was_stopped",
        f"Kidputer Control wasn't running on {device.name} for {_duration(ev.occurred_at - silence)}, while Windows was on. "
        "It may have been stopped by an administrator account.",
        device,
        {"silent_since": silence.isoformat(), "boot_time": boot.isoformat()},
        severity="critical",
    )


@router.post("/events", response_model=AgentEventsOut)
def post_events(body: AgentEventsIn, request: Request, db: DbDep, device: DeviceDep) -> AgentEventsOut:
    accepted = 0
    for ev in sorted(body.events, key=lambda e: e.occurred_at):
        inserted = db.scalar(
            insert(ActivityEvent)
            .values(
                device_id=device.id,
                source="agent",
                event_type=ev.event_type,
                severity=ev.severity,
                message=ev.message,
                data=ev.data,
                occurred_at=ev.occurred_at,
                client_event_id=ev.client_event_id,
            )
            .on_conflict_do_nothing(constraint="uq_events_client_id")
            .returning(ActivityEvent.id)
        )
        if inserted is not None:
            accepted += 1
            _apply_side_effects(db, device, ev)
            queue_for_event(db, event_type=ev.event_type, message=ev.message, device=device, event_id=inserted)
    _touch(db, device, request)
    db.commit()
    return AgentEventsOut(accepted=accepted, duplicates=len(body.events) - accepted)


@router.post("/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat(body: HeartbeatIn, request: Request, db: DbDep, device: DeviceDep) -> None:
    if body.agent_version:
        device.agent_version = body.agent_version
    device.last_status = body.status
    _touch(db, device, request)
    db.commit()



# --- Agent auto-update -----------------------------------------------------------------------------------


@router.get("/update/manifest")
def update_manifest(request: Request, db: DbDep, device: DeviceDep, settings: SettingsDep) -> dict[str, Any]:
    bundle = load_bundle(settings.agent_dir)
    if bundle is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no agent files on this server")
    _touch(db, device, request)
    db.commit()
    return {
        "version": bundle.version,
        "bundle": bundle.fingerprint,
        "files": [{"path": f.path, "sha256": f.sha256, "size": f.size} for f in bundle.files],
    }


@router.get("/update/file")
def update_file(path: str, device: DeviceDep, settings: SettingsDep) -> Response:
    """One agent file. Only files listed in the manifest can be fetched."""
    bundle = load_bundle(settings.agent_dir)
    f = bundle.file(path) if bundle else None
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not an agent file")
    return Response(content=f.source.read_bytes(), media_type="application/octet-stream")


# What the PC may upload in bulk, and how big each may be. Later features add their kinds here.
REPORT_LIMITS: dict[str, int] = {
    "processes": 512 * 1024,
    "usage": 256 * 1024,
}


@router.post("/reports/{kind}", status_code=status.HTTP_204_NO_CONTENT)
async def post_report(kind: str, request: Request, db: DbDep, device: DeviceDep) -> None:
    """Store the latest copy of a bulk report (the running processes, a drive scan, ...)."""
    limit = REPORT_LIMITS.get(kind)
    if limit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown report kind")
    raw = await request.body()
    if len(raw) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "report too large")
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "report must be JSON") from exc
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "report must be a JSON object")
    if kind == "usage":
        save_usage_report(db, device, data, utcnow())
    else:
        save_report(db, device, kind, data, utcnow())
    _touch(db, device, request)
    db.commit()


@router.post("/commands/{command_id}/result", status_code=status.HTTP_204_NO_CONTENT)
def post_command_result(command_id: uuid.UUID, body: CommandResultIn, request: Request, db: DbDep, device: DeviceDep) -> None:
    cmd = db.get(DeviceCommand, command_id, with_for_update=True)
    if cmd is None or cmd.device_id != device.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "command not found")
    if cmd.status == "pending":  # retries of an already-recorded result are ignored
        cmd.status = body.status
        cmd.result = body.result
        cmd.completed_at = utcnow()
    _touch(db, device, request)
    db.commit()
