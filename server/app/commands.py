"""Parent -> PC commands and PC -> server bulk reports, shared by apps, files and installs.

Commands ride in the state snapshot the agent already polls, so they work through any firewall the
agent can reach the server through. While a command is pending, or a parent has a live view open,
the agent polls every few seconds instead of every 30.
"""

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AdminUser, Device, DeviceCommand, DeviceReport

# How long a parent's live view keeps the PC on fast polling after the page's last ping.
LIVE_FOR = timedelta(seconds=60)
# How long the agent polls while live (also while a command is pending).
LIVE_POLL_SECONDS = 3


def create_command(
    db: Session, device: Device, kind: str, payload: dict[str, Any], admin: AdminUser | None, now: datetime, ttl: timedelta
) -> DeviceCommand:
    cmd = DeviceCommand(
        id=uuid.uuid4(), device_id=device.id, kind=kind, payload=payload, created_by=admin.id if admin else None,
        created_at=now, expires_at=now + ttl,
    )
    db.add(cmd)
    return cmd


def pending_commands(db: Session, device: Device, now: datetime) -> list[DeviceCommand]:
    return list(
        db.scalars(
            select(DeviceCommand)
            .where(DeviceCommand.device_id == device.id, DeviceCommand.status == "pending", DeviceCommand.expires_at > now)
            .order_by(DeviceCommand.created_at)
        )
    )


def snapshot_live_until(device: Device, now: datetime) -> str | None:
    """live_until for the snapshot, rounded up to 30 s so pings from an open page don't change the
    snapshot's ETag every time."""
    if device.live_until is None or device.live_until <= now:
        return None
    ts = device.live_until.timestamp()
    rounded = datetime.fromtimestamp(ts - ts % 30 + 30, tz=device.live_until.tzinfo)
    return rounded.isoformat()


def get_report(db: Session, device: Device, kind: str) -> DeviceReport | None:
    return db.get(DeviceReport, (device.id, kind))


def save_report(db: Session, device: Device, kind: str, data: dict[str, Any], now: datetime) -> DeviceReport:
    rep = db.get(DeviceReport, (device.id, kind))
    if rep is None:
        rep = DeviceReport(device_id=device.id, kind=kind, data=data, updated_at=now)
        db.add(rep)
    else:
        rep.data = data
        rep.updated_at = now
    return rep
