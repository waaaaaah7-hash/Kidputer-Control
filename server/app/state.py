"""The desired-state snapshot the agent syncs and caches locally for offline enforcement."""

import base64
import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Device, LockRequest, Message, Override


def _iso(dt: datetime) -> str:
    return dt.isoformat()


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

    return {
        "device_id": str(device.id),
        "name": device.name,
        "timezone": device.timezone,
        "enforcement_enabled": s.enforcement_enabled,
        "countdown_seconds": s.countdown_seconds,
        "poll_interval_seconds": s.poll_interval_seconds,
        "schedule": [
            {"day_of_week": e.day_of_week, "lock_at": e.lock_at.strftime("%H:%M"), "unlock_at": e.unlock_at.strftime("%H:%M")}
            for e in device.schedule
        ],
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
    }


def state_etag(state: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f'"{digest[:32]}"'
