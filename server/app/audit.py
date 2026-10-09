import uuid
from typing import Any

from sqlalchemy.orm import Session

from .deps import utcnow
from .models import ActivityEvent, AdminUser, Device


def log_admin(
    db: Session,
    user: AdminUser | None,
    event_type: str,
    message: str,
    device_id: uuid.UUID | None = None,
    data: dict[str, Any] | None = None,
    severity: str = "info",
) -> None:
    """Record an admin/system action in the central activity log (caller commits)."""
    db.add(
        ActivityEvent(
            device_id=device_id,
            source="admin" if user else "system",
            event_type=event_type,
            severity=severity,
            message=message,
            data=data,
            occurred_at=utcnow(),
            actor_user_id=user.id if user else None,
        )
    )


def log_system(
    db: Session,
    event_type: str,
    message: str,
    device: Device | None = None,
    data: dict[str, Any] | None = None,
    severity: str = "info",
) -> ActivityEvent:
    """Record something the server itself noticed (e.g. "PC went silent") and notify parents (caller commits)."""
    from .notify import queue_for_event  # avoid an import cycle

    ev = ActivityEvent(
        device_id=device.id if device is not None else None,
        source="system",
        event_type=event_type,
        severity=severity,
        message=message,
        data=data,
        occurred_at=utcnow(),
    )
    db.add(ev)
    db.flush()
    queue_for_event(db, event_type=event_type, message=message, device=device, event_id=ev.id)
    return ev
