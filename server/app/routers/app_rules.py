"""App schedules (roadmap #3), custom apps (#4), and usage time (#5)."""

import re
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..audit import log_admin
from ..catalog import CATALOG, CATALOG_BY_ID, CATEGORIES
from ..commands import get_report
from ..deps import AdminDep, DbDep, utcnow
from ..models import AppRule, CustomApp, Device, UsageDaily

router = APIRouter(tags=["app rules"])

TOTAL_KEY = "_total"


# --- Schemas ----------------------------------------------------------------------------------------------


class CatalogAppOut(BaseModel):
    id: str
    name: str
    category: str
    custom: bool
    matchers: list[dict[str, str]]


class CatalogOut(BaseModel):
    categories: dict[str, str]
    apps: list[CatalogAppOut]


class CustomAppIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=60)
    # At least one: the program's file name (e.g. "Game.exe"), text in its product name, or in its path.
    exe: str | None = Field(default=None, max_length=260, pattern=r"^[^\\/:*?\"<>|]+\.exe$")
    product: str | None = Field(default=None, min_length=2, max_length=200)
    path: str | None = Field(default=None, min_length=3, max_length=260)

    @model_validator(mode="after")
    def _something(self) -> "CustomAppIn":
        if not (self.exe or self.product or self.path):
            raise ValueError("Give the program's file name, product name, or folder")
        return self


class Window(BaseModel):
    day: int = Field(ge=0, le=6, description="0 = Monday")
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def _nonempty(self) -> "Window":
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


class RuleIn(BaseModel):
    target_kind: Literal["app", "category"]
    target: str = Field(min_length=1, max_length=80)
    mode: Literal["allow", "block", "schedule"]
    windows: list[Window] = Field(default_factory=list, max_length=42)

    @model_validator(mode="after")
    def _windows_only_for_schedule(self) -> "RuleIn":
        if self.mode == "schedule" and not self.windows:
            raise ValueError("Choose at least one time when it's allowed")
        if self.mode != "schedule":
            self.windows = []
        return self


class RulesIn(BaseModel):
    rules: list[RuleIn] = Field(max_length=200)


class RuleOut(RuleIn):
    id: uuid.UUID
    name: str


class UsageApp(BaseModel):
    key: str
    name: str
    category: str | None
    seconds: int


class UsageDay(BaseModel):
    day: date
    total_seconds: int
    apps: list[UsageApp]


class SeenApp(BaseModel):
    key: str
    name: str
    path: str | None
    exe: str | None
    product: str | None
    company: str | None
    catalog_id: str | None
    last_seen: datetime | None


# --- Helpers --------------------------------------------------------------------------------------------------


def _device(db: Session, device_id: uuid.UUID) -> Device:
    d = db.get(Device, device_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    return d


def _custom_apps(db: Session) -> list[CustomApp]:
    return list(db.scalars(select(CustomApp).order_by(CustomApp.name)))


def all_categories(db: Session) -> dict[str, str]:
    cats = dict(CATEGORIES)
    for c in _custom_apps(db):
        cats.setdefault(c.category, c.category)
    return cats


def app_names(db: Session) -> dict[str, str]:
    names = {a.id: a.name for a in CATALOG}
    names.update({f"custom:{c.id}": c.name for c in _custom_apps(db)})
    return names


def _rule_out(r: AppRule, names: dict[str, str], cats: dict[str, str]) -> RuleOut:
    name = names.get(r.target, r.target) if r.target_kind == "app" else cats.get(r.target, r.target)
    return RuleOut(id=r.id, target_kind=r.target_kind, target=r.target, mode=r.mode, windows=r.windows, name=name)


# --- Catalogue and custom apps ------------------------------------------------------------------------------------


@router.get("/apps/catalog", response_model=CatalogOut)
def catalog(db: DbDep, admin: AdminDep) -> CatalogOut:
    apps = [CatalogAppOut(id=a.id, name=a.name, category=a.category, custom=False, matchers=list(a.matchers)) for a in CATALOG]
    apps += [CatalogAppOut(id=f"custom:{c.id}", name=c.name, category=c.category, custom=True, matchers=c.matchers) for c in _custom_apps(db)]
    return CatalogOut(categories=all_categories(db), apps=apps)


@router.post("/apps/custom", response_model=CatalogAppOut, status_code=status.HTTP_201_CREATED)
def add_custom_app(body: CustomAppIn, db: DbDep, admin: AdminDep) -> CatalogAppOut:
    matcher = {k: v.strip() for k, v in (("exe", body.exe), ("product", body.product), ("path", body.path)) if v and v.strip()}
    c = CustomApp(id=uuid.uuid4(), name=body.name.strip(), category=body.category.strip(), matchers=[matcher], created_by=admin.id)
    db.add(c)
    log_admin(db, admin, "apps.custom_added", f"Custom app added: {c.name} ({c.category})", None, {"matcher": matcher})
    db.commit()
    return CatalogAppOut(id=f"custom:{c.id}", name=c.name, category=c.category, custom=True, matchers=c.matchers)


@router.delete("/apps/custom/{app_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_custom_app(app_id: uuid.UUID, db: DbDep, admin: AdminDep) -> None:
    c = db.get(CustomApp, app_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "app not found")
    db.execute(delete(AppRule).where(AppRule.target_kind == "app", AppRule.target == f"custom:{c.id}"))
    log_admin(db, admin, "apps.custom_removed", f"Custom app removed: {c.name}")
    db.delete(c)
    db.commit()


# --- Rules -----------------------------------------------------------------------------------------------------------


@router.get("/devices/{device_id}/app-rules", response_model=list[RuleOut])
def get_rules(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> list[RuleOut]:
    _device(db, device_id)
    names, cats = app_names(db), all_categories(db)
    rules = db.scalars(select(AppRule).where(AppRule.device_id == device_id).order_by(AppRule.target_kind, AppRule.created_at))
    return [_rule_out(r, names, cats) for r in rules]


@router.put("/devices/{device_id}/app-rules", response_model=list[RuleOut])
def put_rules(device_id: uuid.UUID, body: RulesIn, db: DbDep, admin: AdminDep) -> list[RuleOut]:
    """Replace the PC's app rules. The PC picks them up at its next check-in."""
    device = db.get(Device, device_id, with_for_update=True)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    names, cats = app_names(db), all_categories(db)
    seen: set[tuple[str, str]] = set()
    for r in body.rules:
        known = names if r.target_kind == "app" else cats
        if r.target not in known:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown {r.target_kind}: {r.target}")
        if (r.target_kind, r.target) in seen:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Two rules for {known[r.target]}")
        seen.add((r.target_kind, r.target))
    db.execute(delete(AppRule).where(AppRule.device_id == device.id))
    rows = [
        AppRule(id=uuid.uuid4(), device_id=device.id, target_kind=r.target_kind, target=r.target, mode=r.mode,
                windows=[w.model_dump() for w in r.windows])
        for r in body.rules
    ]
    db.add_all(rows)
    log_admin(db, admin, "config.app_rules_changed", f"App rules updated ({len(rows)} rules)", device.id,
              {"rules": [r.model_dump() for r in body.rules]})
    db.commit()
    return [_rule_out(r, names, cats) for r in rows]


# --- Usage --------------------------------------------------------------------------------------------------------------


_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def save_usage_report(db: Session, device: Device, data: dict[str, Any], now: datetime) -> None:
    """The agent sends running totals per day (in the PC's time zone); the latest totals win."""
    today = now.astimezone(ZoneInfo(device.timezone)).date()
    days = data.get("days")
    if not isinstance(days, dict):
        return
    for day_s, apps in list(days.items())[:10]:
        if not isinstance(day_s, str) or not _DAY.match(day_s) or not isinstance(apps, dict):
            continue
        day = date.fromisoformat(day_s)
        if not (today - timedelta(days=8) <= day <= today + timedelta(days=1)):
            continue
        for key, v in list(apps.items())[:500]:
            if not isinstance(key, str) or not isinstance(v, dict) or len(key) > 120:
                continue
            try:
                seconds = max(0, min(int(v.get("seconds", 0)), 86400))
            except (TypeError, ValueError):
                continue
            name = str(v.get("name") or key)[:200]
            category = v.get("category")
            category = str(category)[:60] if category else None
            path = v.get("path")
            path = str(path)[:1024] if path else None
            stmt = insert(UsageDaily).values(device_id=device.id, day=day, app_key=key, name=name, category=category, seconds=seconds, path=path)
            db.execute(stmt.on_conflict_do_update(
                index_elements=[UsageDaily.device_id, UsageDaily.day, UsageDaily.app_key],
                set_={"seconds": stmt.excluded.seconds, "name": stmt.excluded.name, "category": stmt.excluded.category, "path": stmt.excluded.path},
            ))


@router.get("/devices/{device_id}/usage", response_model=list[UsageDay])
def usage(device_id: uuid.UUID, db: DbDep, admin: AdminDep, days: int = Query(default=7, ge=1, le=31)) -> list[UsageDay]:
    device = _device(db, device_id)
    today = utcnow().astimezone(ZoneInfo(device.timezone)).date()
    rows = db.scalars(
        select(UsageDaily).where(UsageDaily.device_id == device.id, UsageDaily.day > today - timedelta(days=days))
    ).all()
    by_day: dict[date, list[UsageDaily]] = {}
    for r in rows:
        by_day.setdefault(r.day, []).append(r)
    out = []
    for d in sorted(by_day, reverse=True):
        items = by_day[d]
        total = next((r.seconds for r in items if r.app_key == TOTAL_KEY), None)
        apps = sorted((r for r in items if r.app_key != TOTAL_KEY), key=lambda r: -r.seconds)
        out.append(UsageDay(
            day=d,
            total_seconds=total if total is not None else sum(r.seconds for r in apps),
            apps=[UsageApp(key=r.app_key, name=r.name, category=r.category, seconds=r.seconds) for r in apps],
        ))
    return out


@router.get("/devices/{device_id}/apps/seen", response_model=list[SeenApp])
def seen_apps(device_id: uuid.UUID, db: DbDep, admin: AdminDep) -> list[SeenApp]:
    """Apps the child has used here recently: the easy way to add a custom app (#4)."""
    device = _device(db, device_id)
    out: dict[str, SeenApp] = {}
    rep = get_report(db, device, "processes")
    if rep is not None:
        for p in (rep.data or {}).get("processes", []):
            if not isinstance(p, dict) or not p.get("has_window") or p.get("system") or not p.get("path"):
                continue
            path = str(p["path"])
            key = "exe:" + path.rsplit("\\", 1)[-1].lower()
            out.setdefault(key, SeenApp(
                key=key, name=str(p.get("description") or p.get("product") or p.get("name") or key[4:]), path=path,
                exe=path.rsplit("\\", 1)[-1], product=p.get("product"), company=p.get("company"), catalog_id=None,
                last_seen=rep.updated_at,
            ))
    since = utcnow().date() - timedelta(days=30)
    rows = db.execute(
        select(UsageDaily).where(UsageDaily.device_id == device.id, UsageDaily.day >= since, UsageDaily.app_key != TOTAL_KEY)
        .order_by(UsageDaily.day.desc())
    ).scalars().all()
    for r in rows:
        if r.app_key in out:
            continue
        catalog_id = r.app_key if (r.app_key in CATALOG_BY_ID or r.app_key.startswith("custom:")) else None
        exe = r.path.rsplit("\\", 1)[-1] if r.path else (r.app_key[4:] if r.app_key.startswith("exe:") else None)
        out[r.app_key] = SeenApp(key=r.app_key, name=r.name, path=r.path, exe=exe, product=None, company=None,
                                 catalog_id=catalog_id, last_seen=datetime.combine(r.day, time(12, 0), tzinfo=timezone.utc))
    return sorted(out.values(), key=lambda a: a.name.lower())

