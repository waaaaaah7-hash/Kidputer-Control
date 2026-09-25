from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update

from ..audit import log_admin
from ..config import Settings
from ..deps import (
    DbDep,
    SettingsDep,
    active_session,
    admin_for_setup,
    mfa_pending_session,
    utcnow,
)
from ..models import AdminSession, AdminUser
from ..schemas import (
    AdminOut,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    MfaRequest,
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


def _start_session(db: DbDep, request: Request, response: Response, settings: Settings, user: AdminUser, stage: str) -> None:
    token = new_token()
    ttl = timedelta(minutes=settings.mfa_pending_ttl_minutes) if stage == "mfa_pending" else timedelta(hours=settings.session_ttl_hours)
    db.add(
        AdminSession(
            user_id=user.id,
            token_hash=token_hash(token),
            stage=stage,
            expires_at=utcnow() + ttl,
            ip=request.client.host if request.client else None,
            user_agent=(request.headers.get("user-agent") or "")[:512],
        )
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=int(ttl.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/api",
    )


def _end_session(sess: AdminSession) -> None:
    sess.revoked_at = utcnow()


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response, db: DbDep, settings: SettingsDep) -> LoginResponse:
    user = db.scalar(select(AdminUser).where(func.lower(AdminUser.email) == body.email.lower()).with_for_update())
    now = utcnow()
    if user is not None and user.locked_until is not None and user.locked_until > now:
        verify_password(None, body.password)  # keep timing uniform
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "account temporarily locked; try again later")

    if not verify_password(user.password_hash if user else None, body.password) or user is None:
        if user is not None:
            user.failed_logins += 1
            if user.failed_logins >= settings.login_max_failures:
                user.locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
                user.failed_logins = 0
                log_admin(db, None, "admin.login_locked", f"Login locked for {user.email} after repeated failures", severity="warning")
            db.commit()
        raise _BAD_CREDENTIALS

    user.failed_logins = 0
    user.locked_until = None
    if user.totp_enabled:
        _start_session(db, request, response, settings, user, "mfa_pending")
        db.commit()
        return LoginResponse(mfa_required=True)

    _start_session(db, request, response, settings, user, "active")
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
    if user.locked_until is not None and user.locked_until > utcnow():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "account temporarily locked; try again later")
    counter = verify_totp(user.totp_secret, body.code, user.totp_last_counter)
    if counter is None:
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = utcnow() + timedelta(minutes=settings.login_lockout_minutes)
            user.failed_logins = 0
            _end_session(sess)
            log_admin(db, None, "admin.login_locked", f"Login locked for {user.email} after repeated 2FA failures", severity="warning")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid code")

    user.totp_last_counter = counter
    user.failed_logins = 0
    _end_session(sess)
    _start_session(db, request, response, settings, user, "active")
    log_admin(db, user, "admin.login", f"{user.email} signed in (2FA)")
    db.commit()
    return LoginResponse(mfa_required=False)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DbDep, settings: SettingsDep, sess: Annotated[AdminSession, Depends(active_session)]) -> None:
    _end_session(sess)
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/api")


@router.get("/me", response_model=AdminOut)
def me(user: Annotated[AdminUser, Depends(admin_for_setup)]) -> AdminUser:
    return user


@router.post("/totp/setup", response_model=TotpSetupResponse)
def totp_setup(db: DbDep, settings: SettingsDep, user: Annotated[AdminUser, Depends(admin_for_setup)]) -> TotpSetupResponse:
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor authentication is already enabled")
    user.totp_secret = new_totp_secret()
    db.commit()
    return TotpSetupResponse(secret=user.totp_secret, otpauth_uri=totp_uri(user.totp_secret, user.email, settings.totp_issuer))


@router.post("/totp/enable", response_model=AdminOut)
def totp_enable(body: MfaRequest, db: DbDep, user: Annotated[AdminUser, Depends(admin_for_setup)]) -> AdminUser:
    if user.totp_enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor authentication is already enabled")
    if not user.totp_secret:
        raise HTTPException(status.HTTP_409_CONFLICT, "call /auth/totp/setup first")
    counter = verify_totp(user.totp_secret, body.code, None)
    if counter is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid code")
    user.totp_enabled = True
    user.totp_last_counter = counter
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
    # Sign out every other session for this account.
    db.execute(
        update(AdminSession)
        .where(AdminSession.user_id == user.id, AdminSession.id != sess.id, AdminSession.revoked_at.is_(None))
        .values(revoked_at=utcnow())
    )
    log_admin(db, user, "admin.password_changed", f"{user.email} changed their password")
    db.commit()
