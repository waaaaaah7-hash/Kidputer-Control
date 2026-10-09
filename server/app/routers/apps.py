"""Running apps on the child's PC (roadmap #1) and closing them remotely (#2)."""

import uuid
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..audit import log_admin
from ..commands import LIVE_FOR, create_command, get_report
from ..deps import AdminDep, DbDep, utcnow
from ..models import Device, DeviceCommand

router = APIRouter(tags=["apps"])


class ProcessesOut(BaseModel):
    updated_at: datetime | None
    processes: list[dict[str, Any]]
    window_titles: bool


class StopAppIn(BaseModel):
    path: str = Field(min_length=3, max_length=1024)
    name: str = Field(min_length=1, max_length=200)
    # Seconds of warning on the child's screen before the app is closed; 0 = close right away.
    grace_seconds: int = Field(default=60, ge=0, le=600)


class CommandOut(BaseModel):
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    status: Literal["pending", "done", "failed", "expired"]
    result: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


def _device(db, device_id: uuid.UUID) -> Device:
    d = db.get(Device, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return d


def command_out(c: DeviceCommand, now: datetime) -> CommandOut:
    st = "expired" if c.status == "pending" and c.expires_at <= now else c.status
    return CommandOut(id=c.id, kind=c.kind, payload=c.payload, status=st, result=c.result, created_at=c.created_at, completed_at=c.completed_at)


@router.post("/devices/{device_id}/live", status_code=status.HTTP_204_NO_CONTENT)
def keep_live(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> None:
    """Called every ~20 s by a page showing live data: the PC checks in every few seconds meanwhile."""
    d = _device(db, device_id)
    d.live_until = utcnow() + LIVE_FOR
    db.commit()


@router.get("/devices/{device_id}/processes", response_model=ProcessesOut)
def processes(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> ProcessesOut:
    d = _device(db, device_id)
    rep = get_report(db, d, "processes")
    return ProcessesOut(
        updated_at=rep.updated_at if rep else None,
        processes=list((rep.data or {}).get("processes", [])) if rep else [],
        window_titles=d.settings.show_window_titles,
    )


@router.post("/devices/{device_id}/apps/stop", response_model=CommandOut, status_code=status.HTTP_201_CREATED)
def stop_app(device_id: uuid.UUID, body: StopAppIn, db: DbDep, admin: AdminDep) -> CommandOut:
    """Close every copy of an app running in the child's session, after an on-screen warning."""
    d = _device(db, device_id)
    now = utcnow()
    cmd = create_command(db, d, "stop_app", body.model_dump(), admin, now, timedelta(minutes=10) + timedelta(seconds=body.grace_seconds))
    db.flush()
    when = f"after a {body.grace_seconds}-second warning" if body.grace_seconds else "right away"
    log_admin(db, admin, "app.stop_requested", f"Asked to close {body.name} {when}", d.id, {"command_id": str(cmd.id), "path": body.path})
    db.commit()
    return command_out(cmd, now)


@router.get("/devices/{device_id}/commands", response_model=list[CommandOut])
def list_commands(device_id: uuid.UUID, db: DbDep, admin: AdminDep, kind: str | None = None) -> list[CommandOut]:
    _device(db, device_id)
    q = select(DeviceCommand).where(DeviceCommand.device_id == device_id)
    if kind:
        q = q.where(DeviceCommand.kind == kind)
    now = utcnow()
    return [command_out(c, now) for c in db.scalars(q.order_by(DeviceCommand.created_at.desc()).limit(20))]


@router.get("/devices/{device_id}/commands/{command_id}", response_model=CommandOut)
def get_command(device_id: uuid.UUID, command_id: uuid.UUID, db: DbDep, admin: AdminDep) -> CommandOut:
    c = db.get(DeviceCommand, command_id)
    if c is None or c.device_id != device_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "command not found")
    return command_out(c, utcnow())
