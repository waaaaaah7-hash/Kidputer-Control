import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..audit import log_admin
from ..config import Settings
from ..deps import (
    DbDep,
    SettingsDep,
    active_session,
    admin_for_setup,
    current_admin,
    mfa_pending_session,
    utcnow,
)
from ..models import AdminSession, AdminUser, RememberedDevice
from ..schemas import (
    AdminOut,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MfaRequest,
    RememberedDeviceOut,
    SessionOut,
    TotpSetupResponse,
)
from ..security import (
    hash_password,
    new_token,
    new_totp_secret,
    token_hash,
    totp_uri,
    verify_password,
    verify_totp,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_BAD_CREDENTIALS = HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid email or password")

# The "remember this device" cookie is only ever sent to the sign-in endpoints.
_DEVICE_COOKIE_PATH = "/api/v1/auth"


def start_session(
    db: Session,
    request: Request,
    response: Response,
    settings: Settings,
    user: AdminUser,
    stage: str,
    *,
    method: str | None = None,
    verified: bool = False,
    remembered_device_id: uuid.UUID | None = None,
) -> AdminSession:
    """Create a session row and set its cookie. `verified` = a second factor was just used (TOTP or passkey)."""
    token = new_token()
    now = utcnow()
    ttl = timedelta(minutes=settings.mfa_pending_ttl_minutes) if stage == "mfa_pending" else timedelta(hours=settings.session_ttl_hours)
    sess = AdminSession(
        user_id=user.id,
        token_hash=token_hash(token),
        stage=stage,
        expires_at=now + ttl,
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:512],
        auth_method=method,
        verified_at=now if verified else None,
        last_seen_at=now,
        remembered_device_id=remembered_device_id,
    )
    db.add(sess)
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api",
    )
    return sess


def _end_session(sess: AdminSession) -> None:
    sess.revoked_at = utcnow()


def lock_after_failure(db: Session, user: AdminUser, settings: Settings, what: str) -> bool:
    """Count a failed sign-in or re-check; lock the account after too many. Returns True if it just locked."""
    user.failed_logins += 1
    if user.failed_logins < settings.login_max_failures:
        return False
    user.locked_until = utcnow() + timedelta(minutes=settings.login_lockout_minutes)
    user.failed_logins = 0
    log_admin(db, None, "admin.login_locked", f"Login locked for {user.email} after repeated {what} failures", severity="warning")
    return True


def is_locked(user: AdminUser) -> bool:
    return user.locked_until is not None and user.locked_until > utcnow()


# --- Remembered devices ---------------------------------------------------------------------------


def _remembered_device(db: Session, request: Request, settings: Settings, user: AdminUser) -> RememberedDevice | None:
    token = request.cookies.get(settings.device_cookie_name)
    if not token:
        return None
    dev = db.scalar(select(RememberedDevice).where(RememberedDevice.token_hash == token_hash(token)))
    if dev is None or dev.user_id != user.id or dev.revoked_at is not None or dev.expires_at <= utcnow():
        return None
    return dev


def _remember_device(db: Session, request: Request, response: Response, settings: Settings, user: AdminUser) -> RememberedDevice:
    token = new_token()
    ttl = timedelta(days=settings.remember_device_days)
    dev = RememberedDevice(
        id=uuid.uuid4(),
        user_id=user.id,
        token_hash=token_hash(token),
        user_agent=(request.headers.get("user-agent") or "")[:512],
        ip=request.client.host if request.client else None,
        expires_at=utcnow() + ttl,
    )
    db.add(dev)
    response.set_cookie(
        settings.device_cookie_name,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=_DEVICE_COOKIE_PATH,
    )
    log_admin(db, user, "admin.device_remembered", f"{user.email} chose to remember a device for {settings.remember_device_days} days")
    return dev


# --- Sign-in ----------------------------------------------------------------------------------------


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: DbDep, settings: SettingsDep) -> LoginResponse:
    user = db.scalar(select(AdminUser).where(func.lower(AdminUser.email) == body.email.lower()).with_for_update())
    if user is not None and is_locked(user):
        verify_password(None, body.password)  # keep timing uniform
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "account temporarily locked; try again later")

    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        if user is not None:
            lock_after_failure(db, user, settings, "password")
            db.commit()
        raise _BAD_CREDENTIALS

    user.failed_logins = 0
    user.locked_until = None
    if user.totp_enabled:
        dev = _remembered_device(db, request, settings, user)
        if dev is not None:
            # Remembered browser: skip the code. The session isn't "verified", so risky actions still ask.
            dev.last_used_at = utcnow()
            start_session(db, request, response, settings, user, "active", method="password_remembered", remembered_device_id=dev.id)
            log_admin(db, user, "admin.login", f"{user.email} signed in (remembered device)")
            db.commit()
            return LoginResponse(mfa_required=False)
        start_session(db, request, response, settings, user, "mfa_pending")
        db.commit()
        return LoginResponse(mfa_required=True)

    # No second factor yet: only TOTP setup is reachable when it's required (see deps.current_admin).
    start_session(db, request, response, settings, user, "active", method="password", verified=not settings.require_totp)
    log_admin(db, user, "admin.login", f"{user.email} signed in")
    db.commit()
    return LoginResponse(mfa_required=False, totp_setup_required=settings.require_totp)


@router.post("/login/mfa", response_model=LoginResponse)
def login_mfa(
    body: MfaRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    sess: Annotated[AdminSession, Depends(mfa_pending_session)],
) -> LoginResponse:
    user = db.get(AdminUser, sess.user_id, with_for_update=True)
    if is_locked(user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "account temporarily locked; try again later")
    counter = verify_totp(user.totp_secret, body.code, user.totp_last_counter)
    if counter is None:
        if lock_after_failure(db, user, settings, "2FA"):
            _end_session(sess)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid code")

    user.totp_last_counter = counter
    user.failed_logins = 0
    _end_session(sess)
    dev = _remember_device(db, request, response, settings, user) if body.remember else None
    start_session(
        db, request, response, settings, user, "active",
        method="password_totp", verified=True, remembered_device_id=dev.id if dev else None,
    )
    log_admin(db, user, "admin.login", f"{user.email} signed in (2FA)")
    db.commit()
    return LoginResponse(mfa_required=False)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DbDep, settings: SettingsDep, sess: Annotated[AdminSession, Depends(active_session)]) -> None:
    # A remembered device stays remembered: that's the point of it. Forget it from the Account page.
    _end_session(sess)
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/api")


@router.get("/me", response_model=AdminOut)
def me(user: Annotated[AdminUser, Depends(admin_for_setup)]) -> AdminUser:
    return user


@router.post("/reverify", status_code=status.HTTP_204_NO_CONTENT)
def reverify(
    body: MfaRequest,
    db: DbDep,
    settings: SettingsDep,
    sess: Annotated[AdminSession, Depends(active_session)],
    user: Annotated[AdminUser, Depends(current_admin)],
) -> None:
    """Confirm it's you with the authenticator code before a risky action (passkeys: /auth/passkeys/reverify)."""
    user = db.get(AdminUser, user.id, with_for_update=True)
    if is_locked(user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "account temporarily locked; try again later")
    if not user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor authentication is not enabled")
    counter = verify_totp(user.totp_secret, body.code, user.totp_last_counter)
    if counter is None:
        if lock_after_failure(db, user, settings, "2FA"):
            _end_session(sess)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid code")
    user.totp_last_counter = counter
    user.failed_logins = 0
    sess.verified_at = utcnow()
    db.commit()


# --- TOTP setup ----------------------------------------------------------------------------------


@router.post("/totp/setup", response_model=TotpSetupResponse)
def totp_setup(db: DbDep, settings: SettingsDep, user: Annotated[AdminUser, Depends(admin_for_setup)]) -> TotpSetupResponse:
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor authentication is already enabled")
    user.totp_secret = new_totp_secret()
    db.commit()
    return TotpSetupResponse(secret=user.totp_secret, otpauth_uri=totp_uri(user.totp_secret, user.email, settings.totp_issuer))


@router.post("/totp/enable", response_model=AdminOut)
def totp_enable(body: MfaRequest, db: DbDep, sess: Annotated[AdminSession, Depends(active_session)]) -> AdminUser:
    user = sess.user
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor authentication is already enabled")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "call /auth/totp/setup first")
    counter = verify_totp(user.totp_secret, body.code, None)
    if counter is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid code")
    user.totp_enabled = True
    user.totp_last_counter = counter
    sess.verified_at = utcnow()  # the code just proved the second factor
    log_admin(db, user, "admin.totp_enabled", f"{user.email} enabled two-factor authentication")
    db.commit()
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    db: DbDep,
    sess: Annotated[AdminSession, Depends(active_session)],
) -> None:
    user = sess.user
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    now = utcnow()
    # Sign out every other session and forget every remembered device for this account.
    db.execute(
        update(AdminSession)
        .where(AdminSession.user_id == user.id, AdminSession.id != sess.id, AdminSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.execute(
        update(RememberedDevice)
        .where(RememberedDevice.user_id == user.id, RememberedDevice.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    log_admin(db, user, "admin.password_changed", f"{user.email} changed their password")
    db.commit()


# --- Signed-in devices -------------------------------------------------------------------------------


def _live_sessions(db: Session, user_id: uuid.UUID) -> list[AdminSession]:
    return list(
        db.scalars(
            select(AdminSession)
            .where(
                AdminSession.user_id == user_id,
                AdminSession.stage == "active",
                AdminSession.revoked_at.is_(None),
                AdminSession.expires_at > utcnow(),
            )
            .order_by(AdminSession.last_seen_at.desc().nulls_last())
        )
    )


@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(db: DbDep, sess: Annotated[AdminSession, Depends(active_session)]) -> list[SessionOut]:
    return [
        SessionOut(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            expires_at=s.expires_at,
            ip=s.ip,
            user_agent=s.user_agent,
            auth_method=s.auth_method,
            remembered=s.remembered_device_id is not None,
            current=s.id == sess.id,
        )
        for s in _live_sessions(db, sess.user_id)
    ]


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(session_id: uuid.UUID, db: DbDep, sess: Annotated[AdminSession, Depends(active_session)]) -> None:
    target = db.get(AdminSession, session_id)
    if target is None or target.user_id != sess.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    if target.revoked_at is None:
        _end_session(target)
        log_admin(db, sess.user, "admin.session_revoked", "Signed out a session from the signed-in devices page")
        db.commit()


@router.post("/sessions/sign-out-others", status_code=status.HTTP_204_NO_CONTENT)
def sign_out_others(db: DbDep, sess: Annotated[AdminSession, Depends(active_session)]) -> None:
    others = [s for s in _live_sessions(db, sess.user_id) if s.id != sess.id]
    for s in others:
        _end_session(s)
    if others:
        log_admin(db, sess.user, "admin.session_revoked", f"Signed out {len(others)} other session(s)")
    db.commit()


@router.get("/remembered-devices", response_model=list[RememberedDeviceOut])
def list_remembered_devices(
    request: Request, db: DbDep, settings: SettingsDep, sess: Annotated[AdminSession, Depends(active_session)]
) -> list[RememberedDeviceOut]:
    mine = _remembered_device(db, request, settings, sess.user)
    devices = db.scalars(
        select(RememberedDevice)
        .where(RememberedDevice.user_id == sess.user_id, RememberedDevice.revoked_at.is_(None), RememberedDevice.expires_at > utcnow())
        .order_by(RememberedDevice.created_at.desc())
    )
    out = []
    for d in devices:
        item = RememberedDeviceOut.model_validate(d)
        item.current = mine is not None and d.id == mine.id
        out.append(item)
    return out


@router.delete("/remembered-devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
def forget_device(
    device_id: uuid.UUID,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
    sess: Annotated[AdminSession, Depends(active_session)],
) -> None:
    """Forget a remembered device and sign out the sessions that were started with it (e.g. a lost phone)."""
    dev = db.get(RememberedDevice, device_id)
    if dev is None or dev.user_id != sess.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
    mine = _remembered_device(db, request, settings, sess.user)
    now = utcnow()
    dev.revoked_at = dev.revoked_at or now
    db.execute(
        update(AdminSession)
        .where(AdminSession.remembered_device_id == dev.id, AdminSession.id != sess.id, AdminSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    if mine is not None and mine.id == dev.id:
        response.delete_cookie(settings.device_cookie_name, path=_DEVICE_COOKIE_PATH)
    log_admin(db, sess.user, "admin.device_forgotten", "Forgot a remembered device")
    db.commit()
