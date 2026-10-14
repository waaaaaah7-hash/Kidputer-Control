"""Security tab (roadmap #23): a PC's virus protection and Windows updates, and the buttons for them."""

import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from .. import pc_security
from ..audit import log_admin
from ..commands import create_command, get_report
from ..deps import AdminDep, DbDep, utcnow
from ..models import Device, DeviceCommand
from .apps import CommandOut, command_out

router = APIRouter(tags=["security"])

SecurityAction = Literal["quick_scan", "full_scan", "update_signatures", "remove_threats", "check_updates", "install_updates"]


class SecurityOut(BaseModel):
    updated_at: datetime | None
    report: dict[str, Any] | None
    summary: dict[str, Any] | None
    # The latest requests from the dashboard (Quick scan, Install now, ...), newest first.
    commands: list[CommandOut]


def _device(db, device_id: uuid.UUID) -> Device:
    d = db.get(Device, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return d


@router.get("/devices/{device_id}/security", response_model=SecurityOut)
def get_security(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> SecurityOut:
    d = _device(db, device_id)
    now = utcnow()
    rep = get_report(db, d, "security")
    cmds = db.scalars(
        select(DeviceCommand).where(DeviceCommand.device_id == d.id, DeviceCommand.kind == "security").order_by(DeviceCommand.created_at.desc()).limit(10)
    )
    return SecurityOut(
        updated_at=rep.updated_at if rep else None,
        report=rep.data if rep else None,
        summary=pc_security.summary(rep.data, rep.updated_at, now) if rep else None,
        commands=[command_out(c, now) for c in cmds],
    )


@router.post("/devices/{device_id}/security/{action}", response_model=CommandOut, status_code=status.HTTP_201_CREATED)
def request_action(device_id: uuid.UUID, action: SecurityAction, db: DbDep, admin: AdminDep) -> CommandOut:
    """Ask the PC for a scan, a definitions update, removing threats, or checking for or installing updates."""
    d = _device(db, device_id)
    now = utcnow()
    cmd = create_command(db, d, "security", {"action": action}, admin, now, timedelta(days=1))
    db.flush()
    log_admin(db, admin, "security.requested", f"Asked for {pc_security.ACTION_LABELS[action]}", d.id, {"command_id": str(cmd.id), "action": action})
    db.commit()
    return command_out(cmd, now)
