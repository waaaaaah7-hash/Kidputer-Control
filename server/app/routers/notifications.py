"""Each parent's notification settings, push subscriptions, and a test message."""

from datetime import datetime, time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, select

from .. import notify
from ..deps import AdminDep, DbDep, SettingsDep
from ..models import AdminUser, Notification, PushSubscription

router = APIRouter(prefix="/notifications", tags=["notifications"])

Level = Literal["immediate", "digest", "off"]


class CategoryOut(BaseModel):
    id: str
    label: str
    description: str
    default: Level


class NotificationSettingsOut(BaseModel):
    email_enabled: bool
    push_enabled: bool
    levels: dict[str, Level]
    quiet_start: time | None
    quiet_end: time | None
    timezone: str
    digest_hour: int
    categories: list[CategoryOut]
    email_available: bool
    email_address: str
    push_devices: int
    vapid_public_key: str


class NotificationSettingsIn(BaseModel):
    email_enabled: bool
    push_enabled: bool
    levels: dict[str, Level]
    quiet_start: time | None = None
    quiet_end: time | None = None
    timezone: str = "UTC"
    digest_hour: int = Field(ge=0, le=23)

    @field_validator("levels")
    @classmethod
    def _known(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - set(notify.CATEGORY_BY_ID)
        if unknown:
            raise ValueError(f"unknown categories: {', '.join(sorted(unknown))}")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown time zone") from exc
        return v


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=200)
    auth: str = Field(min_length=8, max_length=100)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=2000, pattern=r"^https://")
    keys: PushKeys


class PushUnsubscribe(BaseModel):
    endpoint: str = Field(max_length=2000)


class TestResult(BaseModel):
    email: bool
    push: int
    email_error: str | None = None


class NotificationOut(BaseModel):
    id: int
    category: str
    level: str
    title: str
    body: str
    url: str | None
    created_at: datetime
    sent_at: datetime | None


def _out(db, settings, user: AdminUser) -> NotificationSettingsOut:
    ns = notify.settings_for(db, user)
    devices = len(db.scalars(select(PushSubscription.id).where(PushSubscription.user_id == user.id)).all())
    out = NotificationSettingsOut(
        email_enabled=ns.email_enabled,
        push_enabled=ns.push_enabled,
        levels={c.id: notify.level_for(ns, c.id) for c in notify.CATEGORIES},
        quiet_start=ns.quiet_start,
        quiet_end=ns.quiet_end,
        timezone=ns.timezone,
        digest_hour=ns.digest_hour,
        categories=[CategoryOut(id=c.id, label=c.label, description=c.description, default=c.default) for c in notify.CATEGORIES],
        email_available=settings.email_configured,
        email_address=user.email,
        push_devices=devices,
        vapid_public_key=notify.vapid_public_key(db),
    )
    db.commit()
    return out


@router.get("/settings", response_model=NotificationSettingsOut)
def get_settings_(db: DbDep, settings: SettingsDep, user: AdminDep) -> NotificationSettingsOut:
    return _out(db, settings, user)


@router.put("/settings", response_model=NotificationSettingsOut)
def put_settings(body: NotificationSettingsIn, db: DbDep, settings: SettingsDep, user: AdminDep) -> NotificationSettingsOut:
    ns = notify.settings_for(db, user)
    ns.email_enabled = body.email_enabled
    ns.push_enabled = body.push_enabled
    ns.levels = dict(body.levels)
    ns.quiet_start = body.quiet_start
    ns.quiet_end = body.quiet_end
    ns.timezone = body.timezone
    ns.digest_hour = body.digest_hour
    db.commit()
    return _out(db, settings, user)


@router.post("/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def subscribe(body: PushSubscriptionIn, request: Request, db: DbDep, user: AdminDep) -> None:
    """Called by the browser (or installed phone app) after the parent allows notifications."""
    existing = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if existing is None:
        existing = PushSubscription(endpoint=body.endpoint, user_id=user.id, p256dh="", auth="")
        db.add(existing)
    # The same browser signed in as another parent now belongs to this one.
    existing.user_id = user.id
    existing.p256dh = body.keys.p256dh
    existing.auth = body.keys.auth
    existing.user_agent = (request.headers.get("user-agent") or "")[:512]
    db.commit()


@router.post("/push-subscriptions/remove", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(body: PushUnsubscribe, db: DbDep, user: AdminDep) -> None:
    db.execute(delete(PushSubscription).where(PushSubscription.endpoint == body.endpoint, PushSubscription.user_id == user.id))
    db.commit()


@router.post("/test", response_model=TestResult)
def send_test(db: DbDep, settings: SettingsDep, user: AdminDep) -> TestResult:
    ns = notify.settings_for(db, user)
    if not (ns.email_enabled and settings.email_configured) and not ns.push_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Turn on email or push notifications first.")
    r = notify.deliver(db, settings, user, ns, "Kidputer Control test", "Notifications are working.", "/account/notifications")
    db.commit()
    return TestResult(**r)


@router.get("", response_model=list[NotificationOut])
def recent(db: DbDep, user: AdminDep, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[Notification]:
    return list(
        db.scalars(select(Notification).where(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(limit))
    )
