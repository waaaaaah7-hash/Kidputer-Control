"""The desired-state snapshot the agent syncs and caches locally for offline enforcement."""

import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .commands import pending_commands, snapshot_live_until
from .models import Device, LockRequest, Message, Override, ScheduleEntry

# How far ahead lock windows are precomputed. The agent falls back to evaluating the weekly schedule
# in the PC's local time only if it has been offline for longer than this.
LOCK_WINDOW_HORIZON = timedelta(days=14)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def lock_windows(entries: list[ScheduleEntry], tz_name: str, now: datetime) -> tuple[list[dict[str, str]], datetime]:
    """Expand the weekly schedule into absolute UTC windows covering [now, now + horizon).

    Done server-side because Windows PowerShell 5.1 has no IANA time zone support, and the child could
    otherwise dodge the schedule by changing the PC's time zone (standard users are allowed to).
    """
    tz = ZoneInfo(tz_name)
    until = now + LOCK_WINDOW_HORIZON
    by_day = {e.day_of_week: e for e in entries}
    windows = []
    today = now.astimezone(tz).date()
    for offset in range(-1, LOCK_WINDOW_HORIZON.days + 1):
        day: date = today + timedelta(days=offset)
        entry = by_day.get(day.weekday())
        if entry is None:
            continue
        end_day = day if entry.unlock_at > entry.lock_at else day + timedelta(days=1)
        start = datetime.combine(day, entry.lock_at, tz)
        end = datetime.combine(end_day, entry.unlock_at, tz)
        if end > now and start < until:
            windows.append({"start": _iso(start), "end": _iso(end)})
    return windows, until


def build_state(db: Session, device: Device, now: datetime) -> dict[str, Any]:
    s = device.settings
    overrides = db.scalars(
        select(Override)
        .where(Override.device_id == device.id, Override.cancelled_at.is_(None), Override.ends_at > now)
        .order_by(Override.starts_at)
    ).all()
    message = db.scalar(
        select(Message).where(
            Message.device_id == device.id, Message.cleared_at.is_(None), Message.dismissed_at.is_(None)
        )
    )
    lock = db.scalar(select(LockRequest).where(LockRequest.device_id == device.id, LockRequest.cleared_at.is_(None)))
    # Truncate to the hour so the ETag (a hash of this snapshot) only changes when the windows do.
    windows, windows_until = lock_windows(device.schedule, device.timezone, now.replace(minute=0, second=0, microsecond=0))

    return {
        "device_id": str(device.id),
        "name": device.name,
        "timezone": device.timezone,
        "mode": device.mode,
        "enforcement_enabled": s.enforcement_enabled,
        "countdown_seconds": s.countdown_seconds,
        "poll_interval_seconds": s.poll_interval_seconds,
        "schedule": [
            {"day_of_week": e.day_of_week, "lock_at": e.lock_at.strftime("%H:%M"), "unlock_at": e.unlock_at.strftime("%H:%M")}
            for e in device.schedule
        ],
        "lock_windows": windows,
        "lock_windows_until": _iso(windows_until),
        "pin": {
            "algorithm": "pbkdf2-sha256",
            "hash": base64.b64encode(s.pin_hash).decode() if s.pin_hash else None,
            "salt": base64.b64encode(s.pin_salt).decode() if s.pin_salt else None,
            "iterations": s.pin_iterations,
            "max_attempts": s.pin_max_attempts,
            "lockout_minutes": s.pin_lockout_minutes,
            "override_minutes": s.pin_override_minutes,
        },
        "overrides": [{"id": str(o.id), "starts_at": _iso(o.starts_at), "ends_at": _iso(o.ends_at)} for o in overrides],
        "message": {"id": str(message.id), "body": message.body, "mode": message.mode} if message else None,
        "immediate_lock": {"id": str(lock.id), "reason": lock.reason, "requested_at": _iso(lock.created_at)} if lock else None,
        "show_window_titles": s.show_window_titles,
        "live_until": snapshot_live_until(device, now),
        "commands": [{"id": str(c.id), "kind": c.kind, "payload": c.payload} for c in pending_commands(db, device, now)],
    }


def state_etag(state: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f'"{digest[:32]}"'
