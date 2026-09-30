from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .db import get_db
from .models import AdminSession, AdminUser, Device
from .security import token_hash

DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Browsers cannot attach a custom header to a cross-site request without a CORS preflight, which we
# don't grant to foreign origins. Requiring it on state-changing cookie-authenticated calls blocks CSRF.
CSRF_HEADER = "X-Kidputer-Control"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_session(request: Request, db: Session, settings: Settings, stage: str) -> AdminSession:
    if request.method not in _SAFE_METHODS and CSRF_HEADER.lower() not in request.headers:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing {CSRF_HEADER} header")
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    sess = db.scalar(select(AdminSession).where(AdminSession.token_hash == token_hash(token)))
    if sess is None or sess.revoked_at is not None or sess.expires_at <= utcnow() or sess.stage != stage:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
    return sess


def mfa_pending_session(request: Request, db: DbDep, settings: SettingsDep) -> AdminSession:
    return _load_session(request, db, settings, "mfa_pending")


def active_session(request: Request, db: DbDep, settings: SettingsDep) -> AdminSession:
    return _load_session(request, db, settings, "active")


def admin_for_setup(sess: Annotated[AdminSession, Depends(active_session)]) -> AdminUser:
    """Password-authenticated admin, possibly still without 2FA. Only for account/2FA setup routes."""
    return sess.user


def current_admin(user: Annotated[AdminUser, Depends(admin_for_setup)], settings: SettingsDep) -> AdminUser:
    if settings.require_totp and not user.totp_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "two-factor authentication must be enabled first")
    return user


AdminDep = Annotated[AdminUser, Depends(current_admin)]


def current_device(db: DbDep, authorization: Annotated[str | None, Header()] = None) -> Device:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "device token required", headers={"WWW-Authenticate": "Bearer"})
    device = db.scalar(select(Device).where(Device.token_hash == token_hash(token)))
    if device is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid device token", headers={"WWW-Authenticate": "Bearer"})
    return device


DeviceDep = Annotated[Device, Depends(current_device)]
