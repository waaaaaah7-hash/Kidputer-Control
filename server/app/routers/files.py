"""Drives, folder sizes and a file explorer for the child's PC (roadmap #19).

Names, sizes and dates only: there's deliberately no way to open, preview or download a file, for the
child's privacy, and so the dashboard can't become a way to pull files off the PC.
"""

import re
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field, field_validator

from ..audit import log_admin
from ..commands import create_command, get_report
from ..deps import AdminDep, DbDep, utcnow
from ..models import Device
from .apps import CommandOut, command_out

router = APIRouter(tags=["files"])

_LETTER = re.compile(r"^[A-Z]$")


class DrivesOut(BaseModel):
    updated_at: datetime | None
    drives: list[dict[str, Any]]
    devices: list[str]
    scans: dict[str, dict[str, Any]]
    scanning: dict[str, Any] | None


class FolderChange(BaseModel):
    path: str
    size: int
    previous: int | None


class ScanOut(BaseModel):
    letter: str
    scanned_at: datetime
    scan: dict[str, Any]
    previous_scanned_at: datetime | None
    changes: list[FolderChange]


class ListFolderIn(BaseModel):
    path: str = Field(min_length=3, max_length=1024)

    @field_validator("path")
    @classmethod
    def _local_absolute(cls, v: str) -> str:
        v = v.replace("/", "\\")
        if not re.match(r"^[A-Za-z]:\\", v) or any(part == ".." for part in v.split("\\")):
            raise ValueError("Use a full folder path on one of the PC's drives, like C:\\Users")
        return v


def _device(db, device_id: uuid.UUID) -> Device:
    d = db.get(Device, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return d


def _flatten(node: dict[str, Any], prefix: str, out: dict[str, int], depth: int = 0) -> None:
    path = node.get("n", "") if depth == 0 else f"{prefix.rstrip(chr(92))}\\{node.get('n', '')}"
    out[path] = int(node.get("s") or 0)
    for c in node.get("c") or []:
        if isinstance(c, dict):
            _flatten(c, path, out, depth + 1)


def folder_changes(current: dict[str, Any], previous: dict[str, Any] | None, limit: int = 15) -> list[FolderChange]:
    """The folders that grew (or appeared) most since the previous scan. A folder only counts as new if
    its parent was in the previous scan too, so one new folder isn't reported at every level."""
    if not previous or not isinstance(current.get("tree"), dict) or not isinstance(previous.get("tree"), dict):
        return []
    cur: dict[str, int] = {}
    old: dict[str, int] = {}
    _flatten(current["tree"], "", cur)
    _flatten(previous["tree"], "", old)
    grew: dict[str, tuple[int, FolderChange]] = {}
    for path, size in cur.items():
        before = old.get(path)
        if before is None:
            parent = path.rsplit("\\", 1)[0]
            if parent not in old and parent + "\\" not in old:
                continue
            growth = size
        else:
            growth = size - before
        if growth >= 50 * 1024 * 1024:
            grew[path] = (growth, FolderChange(path=path, size=size, previous=before))
    # A folder's growth also shows in every folder above it. Leave a folder out when most of its growth
    # (75%+) is explained by one folder inside it that's listed too, so the specific folders show.
    kept = []
    for path, (growth, ch) in grew.items():
        inside = path.rstrip("\\") + "\\"
        if any(other.startswith(inside) and g >= 0.75 * growth for other, (g, _) in grew.items()):
            continue
        kept.append((growth, ch))
    kept.sort(key=lambda c: -c[0])
    return [ch for _, ch in kept[:limit]]


@router.get("/devices/{device_id}/drives", response_model=DrivesOut)
def drives(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> DrivesOut:
    d = _device(db, device_id)
    rep = get_report(db, d, "drives")
    data = rep.data if rep else {}
    scans: dict[str, dict[str, Any]] = {}
    for drive in data.get("drives", []) if isinstance(data.get("drives"), list) else []:
        letter = str(drive.get("name", ""))[:1].upper()
        if _LETTER.match(letter) and (scan := get_report(db, d, f"scan:{letter}")) is not None:
            tree = scan.data.get("tree") or {}
            scans[letter] = {"scanned_at": scan.updated_at, "size": tree.get("s"), "files": tree.get("f"), "folders": tree.get("d")}
    scanning = (d.last_status or {}).get("scan")
    return DrivesOut(
        updated_at=rep.updated_at if rep else None,
        drives=[x for x in data.get("drives", []) if isinstance(x, dict)],
        devices=[str(x) for x in data.get("devices", []) if isinstance(x, str)],
        scans=scans,
        scanning=scanning if isinstance(scanning, dict) else None,
    )


@router.get("/devices/{device_id}/scans/{letter}", response_model=ScanOut)
def get_scan(device_id: uuid.UUID, letter: str, db: DbDep, admin: AdminDep) -> ScanOut:
    d = _device(db, device_id)
    letter = letter.upper()
    scan = get_report(db, d, f"scan:{letter}") if _LETTER.match(letter) else None
    if scan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This drive hasn't been scanned yet.")
    prev = get_report(db, d, f"scan_prev:{letter}")
    return ScanOut(
        letter=letter,
        scanned_at=scan.updated_at,
        scan=scan.data,
        previous_scanned_at=prev.updated_at if prev else None,
        changes=folder_changes(scan.data, prev.data if prev else None),
    )


@router.post("/devices/{device_id}/scans/{letter}", response_model=CommandOut, status_code=status.HTTP_201_CREATED)
def start_scan(device_id: uuid.UUID, letter: str, db: DbDep, admin: AdminDep) -> CommandOut:
    d = _device(db, device_id)
    letter = letter.upper()
    if not _LETTER.match(letter):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "drive letter A-Z")
    now = utcnow()
    cmd = create_command(db, d, "scan_drive", {"drive": letter}, admin, now, timedelta(days=1))
    db.flush()
    log_admin(db, admin, "files.scan_requested", f"Asked to scan drive {letter}:", d.id, {"command_id": str(cmd.id)})
    db.commit()
    return command_out(cmd, now)


@router.post("/devices/{device_id}/files/list", response_model=CommandOut, status_code=status.HTTP_201_CREATED)
def list_folder(device_id: uuid.UUID, body: ListFolderIn, db: DbDep, admin: AdminDep) -> CommandOut:
    """Ask the PC for one folder's contents (names, sizes, dates). Poll the command for the result."""
    d = _device(db, device_id)
    now = utcnow()
    cmd = create_command(db, d, "list_folder", {"path": body.path}, admin, now, timedelta(minutes=2))
    db.commit()
    return command_out(cmd, now)
