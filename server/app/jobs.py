"""Background jobs, run every 30 seconds by one thread in the API process.

- "PC went silent": a PC that stops checking in raises an alert. This is the one signal an
  administrator child (Trusted mode) can't suppress from the PC itself.
- Sending notifications, and the daily digests.

A Postgres advisory lock makes sure only one process runs them, even with several workers.
"""

import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import notify
from .audit import log_system
from .config import Settings
from .db import SessionLocal
from .deps import utcnow
from .models import ActivityEvent, Device, DeviceSettings

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 30
_JOBS_LOCK = 0x4B434A4F4253  # "KCJOBS"

# Agent events that mean the PC is about to go quiet on purpose.
_SHUTDOWN_EVENTS = ("pc.shutting_down",)


def _local(dt: datetime, tz: str) -> str:
    return dt.astimezone(ZoneInfo(tz)).strftime("%a %H:%M")


def check_silent_devices(db: Session, now: datetime) -> int:
    """Flag PCs that stopped checking in. Returns how many newly went silent."""
    rows = db.execute(
        select(Device, DeviceSettings.silent_alert_minutes)
        .join(DeviceSettings, DeviceSettings.device_id == Device.id)
        .where(
            Device.token_hash.is_not(None),
            Device.last_seen_at.is_not(None),
            Device.silent_since.is_(None),
            DeviceSettings.silent_alert_minutes > 0,
        )
    ).all()
    flagged = 0
    for device, minutes in rows:
        if now - device.last_seen_at < timedelta(minutes=minutes):
            continue
        device.silent_since = device.last_seen_at
        last = db.scalar(
            select(ActivityEvent)
            .where(ActivityEvent.device_id == device.id, ActivityEvent.source == "agent")
            .order_by(ActivityEvent.occurred_at.desc())
            .limit(1)
        )
        since = _local(device.last_seen_at, device.timezone)
        if last is not None and last.event_type in _SHUTDOWN_EVENTS and device.last_seen_at - last.occurred_at < timedelta(minutes=5):
            log_system(db, "device.offline", f"{device.name} was shut down at {since}.", device)
        else:
            log_system(
                db,
                "device.silent",
                f"No contact from {device.name} since {since}. It may be turned off or offline, or Kidputer Control may have "
                "been stopped. You'll hear when it's back.",
                device,
                {"last_seen_at": device.last_seen_at.isoformat()},
                severity="critical",
            )
        flagged += 1
    return flagged


def run_jobs_once(settings: Settings, now: datetime | None = None) -> bool:
    """Run every job once. Returns False if another process holds the jobs lock."""
    now = now or utcnow()
    with SessionLocal() as db:
        if not db.scalar(select(func.pg_try_advisory_xact_lock(_JOBS_LOCK))):
            return False
        check_silent_devices(db, now)
        db.flush()
        notify.dispatch(db, settings, now)
        notify.send_digests(db, settings, now)
        notify.prune(db, now)
        db.commit()
    return True


def start_background_jobs(settings: Settings) -> threading.Event:
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(INTERVAL_SECONDS):
            try:
                run_jobs_once(settings)
            except Exception:  # keep the loop alive; the next run retries
                log.exception("background jobs failed")

    threading.Thread(target=loop, name="kc-jobs", daemon=True).start()
    return stop
