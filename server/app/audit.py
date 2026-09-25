import uuid
from typing import Any

from sqlalchemy.orm import Session

from .deps import utcnow
from .models import ActivityEvent, AdminUser


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
