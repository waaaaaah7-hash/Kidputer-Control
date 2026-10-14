"""The desired-state snapshot the agent syncs and caches locally for offline enforcement."""

import base64
import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .agent_bundle import load_bundle
from .commands import pending_commands, snapshot_live_until
from .config import get_settings
from .catalog import CATALOG
from .models import AppRule, CustomApp, Device, LockRequest, Message, Override, ScheduleEntry

# How far ahead lock windows are precomputed. The agent falls back to evaluating the weekly schedule
# in the PC's local time only if it has been offline for longer than this.
LOCK_WINDOW_HORIZON = timedelta(days=14)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def expand_weekly(weekly: list[tuple[int, time, time]], tz_name: str, now: datetime) -> tuple[list[dict[str, str]], datetime]:
    """Expand weekly (weekday, start, end) local-time windows into absolute UTC windows covering
    [now, now + horizon). end <= start means the window runs past midnight.

    Done server-side because Windows PowerShell 5.1 has no IANA time zone support, and the child could
    otherwise dodge a schedule by changing the PC's time zone (standard users are allowed to).
    """
    tz = ZoneInfo(tz_name)
    until = now + LOCK_WINDOW_HORIZON
    windows = []
    today = now.astimezone(tz).date()
    for offset in range(-1, LOCK_WINDOW_HORIZON.days + 1):
        day: date = today + timedelta(days=offset)
        for dow, start_t, end_t in weekly:
            if dow != day.weekday():
                continue
            end_day = day if end_t > start_t else day + timedelta(days=1)
            start = datetime.combine(day, start_t, tz)
            end = datetime.combine(end_day, end_t, tz)
            if end > now and start < until:
                windows.append({"start": _iso(start), "end": _iso(end)})
    windows.sort(key=lambda w: w["start"])
    return windows, until


def lock_windows(entries: list[ScheduleEntry], tz_name: str, now: datetime) -> tuple[list[dict[str, str]], datetime]:
    """The bedtime schedule as absolute UTC lock windows (see expand_weekly)."""
    return expand_weekly([(e.day_of_week, e.lock_at, e.unlock_at) for e in entries], tz_name, now)


def day_starts(tz_name: str, now: datetime) -> list[dict[str, str]]:
    """Each local day from yesterday to the horizon, with the UTC instant it starts: lets the agent
    count usage per day in the PC's time zone without IANA support (and without trusting the PC's
    own zone, which the child can change)."""
    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()
    days = [today + timedelta(days=d) for d in range(-1, LOCK_WINDOW_HORIZON.days + 2)]
    return [{"day": d.isoformat(), "start": _iso(datetime.combine(d, time(0, 0), tz))} for d in days]


def app_catalog(db: Session) -> list[dict[str, Any]]:
    """Every app the PC should recognise: the built-in catalogue plus the parents' custom apps."""
    apps = [a.to_agent() for a in CATALOG]
    for c in db.scalars(select(CustomApp).order_by(CustomApp.created_at)):
        apps.append({"id": f"custom:{c.id}", "name": c.name, "category": c.category, "matchers": c.matchers})
    return apps


def app_rules_snapshot(db: Session, device: Device, now: datetime) -> list[dict[str, Any]]:
    """The PC's app rules, app-specific ones first (an app's own rule beats its category's)."""
    rules = db.scalars(select(AppRule).where(AppRule.device_id == device.id)).all()
    out = []
    for r in sorted(rules, key=lambda r: (r.target_kind != "app", r.target)):
        weekly = [(w["day"], time.fromisoformat(w["start"]), time.fromisoformat(w["end"])) for w in r.windows]
        allowed, _ = expand_weekly(weekly, device.timezone, now) if r.mode == "schedule" else ([], None)
        out.append({"id": str(r.id), "target_kind": r.target_kind, "target": r.target, "mode": r.mode,
                    "windows": r.windows, "allowed": allowed})
    return out


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
    hour = now.replace(minute=0, second=0, microsecond=0)
    windows, windows_until = lock_windows(device.schedule, device.timezone, hour)

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
        "agent_update": _agent_update(s.auto_update),
        "scan_every_days": s.scan_every_days,
        "security": {
            "updates_mode": s.updates_mode,
            "feature_updates": s.feature_updates,
            "restart_for_updates": s.restart_for_updates,
            "weekly_full_scan": s.weekly_full_scan,
        },
        "app_catalog": app_catalog(db),
        "app_rules": app_rules_snapshot(db, device, hour),
        "day_starts": day_starts(device.timezone, hour),
    }


def _agent_update(enabled: bool) -> dict[str, str] | None:
    """The agent version this PC should be running, if it updates automatically."""
    bundle = load_bundle(get_settings().agent_dir) if enabled else None
    return {"version": bundle.version, "bundle": bundle.fingerprint} if bundle else None


def state_etag(state: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f'"{digest[:32]}"'
