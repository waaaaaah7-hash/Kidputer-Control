"""Parent accounts: list, invite by one-time link, accept an invite, remove.

Every parent sees and manages every PC (one household). A removed parent's account is kept but
disabled, so the activity log still shows who did what.
"""

import uuid
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..audit import log_admin
from ..deps import DbDep, SettingsDep, VerifiedAdminDep, active_session, current_admin, utcnow
from ..models import AdminInvite, AdminSession, AdminUser, Passkey, RememberedDevice
from ..schemas import AdminOut, InviteAccept, InviteCreate, InviteCreated, InviteInfo, InviteOut, InviteToken, ParentOut
from ..security import hash_password, new_token, token_hash
from .auth import start_session

router = APIRouter(tags=["parents"])

_INVITE_GONE = HTTPException(
    status.HTTP_410_GONE, "This invite link has expired or was already used. Ask for a new one."
)


def _active_admin_by_email(db: Session, email: str) -> AdminUser | None:
    return db.scalar(select(AdminUser).where(func.lower(AdminUser.email) == email.lower(), AdminUser.disabled_at.is_(None)))


def _invite_out(db: Session, inv: AdminInvite) -> InviteOut:
    by = db.get(AdminUser, inv.created_by) if inv.created_by else None
    return InviteOut(id=inv.id, email=inv.email, invited_by=by.name if by else None, created_at=inv.created_at, expires_at=inv.expires_at)


def _usable_invite(db: Session, token: str, lock: bool = False) -> AdminInvite:
    q = select(AdminInvite).where(AdminInvite.token_hash == token_hash(token))
    inv = db.scalar(q.with_for_update() if lock else q)
    if inv is None or inv.accepted_at is not None or inv.revoked_at is not None or inv.expires_at <= utcnow():
        raise _INVITE_GONE
    return inv


# --- Signed-in parents ---------------------------------------------------------------------------------


@router.get("/parents", response_model=list[ParentOut])
def list_parents(db: DbDep, user: Annotated[AdminUser, Depends(current_admin)]) -> list[ParentOut]:
    passkeys = dict(db.execute(select(Passkey.user_id, func.count()).group_by(Passkey.user_id)).all())
    last_active = dict(
        db.execute(select(AdminSession.user_id, func.max(AdminSession.last_seen_at)).group_by(AdminSession.user_id)).all()
    )
    parents = db.scalars(select(AdminUser).where(AdminUser.disabled_at.is_(None)).order_by(AdminUser.created_at))
    return [
        ParentOut(
            id=p.id,
            email=p.email,
            name=p.name,
            totp_enabled=p.totp_enabled,
            passkeys=passkeys.get(p.id, 0),
            created_at=p.created_at,
            last_active_at=last_active.get(p.id),
            you=p.id == user.id,
        )
        for p in parents
    ]


@router.get("/parents/invites", response_model=list[InviteOut])
def list_invites(db: DbDep, user: Annotated[AdminUser, Depends(current_admin)]) -> list[InviteOut]:
    pending = db.scalars(
        select(AdminInvite)
        .where(AdminInvite.accepted_at.is_(None), AdminInvite.revoked_at.is_(None), AdminInvite.expires_at > utcnow())
        .order_by(AdminInvite.created_at.desc())
    )
    return [_invite_out(db, i) for i in pending]


@router.post("/parents/invites", response_model=InviteCreated, status_code=status.HTTP_201_CREATED)
def create_invite(body: InviteCreate, request: Request, db: DbDep, settings: SettingsDep, user: VerifiedAdminDep) -> InviteCreated:
    """Adding a parent gives full control of every PC, so it needs a recent passkey or code check."""
    email = str(body.email)
    if _active_admin_by_email(db, email) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That person is already a parent here.")
    now = utcnow()
    # Only the newest invite for an address works.
    db.execute(
        update(AdminInvite)
        .where(func.lower(AdminInvite.email) == email.lower(), AdminInvite.accepted_at.is_(None), AdminInvite.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    token = new_token()
    inv = AdminInvite(
        id=uuid.uuid4(),
        email=email,
        token_hash=token_hash(token),
        created_by=user.id,
        created_at=now,
        expires_at=now + timedelta(hours=settings.invite_ttl_hours),
    )
    db.add(inv)
    log_admin(db, user, "admin.invite_created", f"{user.name} invited {email} to be a parent")
    db.commit()
    base = (settings.public_url or str(request.base_url)).rstrip("/")
    return InviteCreated(invite=_invite_out(db, inv), link=f"{base}/invite#{token}")


@router.delete("/parents/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_invite(invite_id: uuid.UUID, db: DbDep, user: Annotated[AdminUser, Depends(current_admin)]) -> None:
    inv = db.get(AdminInvite, invite_id)
    if inv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "invite not found")
    if inv.accepted_at is None and inv.revoked_at is None:
        inv.revoked_at = utcnow()
        log_admin(db, user, "admin.invite_cancelled", f"{user.name} cancelled the invite for {inv.email}")
        db.commit()


@router.delete("/parents/{parent_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_parent(
    parent_id: uuid.UUID, db: DbDep, user: VerifiedAdminDep, sess: Annotated[AdminSession, Depends(active_session)]
) -> None:
    if parent_id == user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You can't remove yourself. Ask another parent to do it.")
    target = db.get(AdminUser, parent_id, with_for_update=True)
    if target is None or target.disabled_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "parent not found")
    now = utcnow()
    target.disabled_at = now
    db.execute(update(AdminSession).where(AdminSession.user_id == target.id, AdminSession.revoked_at.is_(None)).values(revoked_at=now))
    db.execute(
        update(RememberedDevice).where(RememberedDevice.user_id == target.id, RememberedDevice.revoked_at.is_(None)).values(revoked_at=now)
    )
    db.execute(delete(Passkey).where(Passkey.user_id == target.id))
    log_admin(db, user, "admin.parent_removed", f"{user.name} removed {target.name} ({target.email}) as a parent")
    db.commit()


# --- Accepting an invite (no sign-in: the link is the credential) ------------------------------------------


@router.post("/invites/lookup", response_model=InviteInfo)
def lookup_invite(body: InviteToken, db: DbDep) -> InviteInfo:
    inv = _usable_invite(db, body.token)
    by = db.get(AdminUser, inv.created_by) if inv.created_by else None
    return InviteInfo(email=inv.email, invited_by=by.name if by else None, expires_at=inv.expires_at)


@router.post("/invites/accept", response_model=AdminOut)
def accept_invite(body: InviteAccept, request: Request, response: Response, db: DbDep, settings: SettingsDep) -> AdminUser:
    """Create the account (or bring back a removed one) and sign in; 2FA setup comes next, as for everyone."""
    inv = _usable_invite(db, body.token, lock=True)
    existing = db.scalar(select(AdminUser).where(func.lower(AdminUser.email) == inv.email.lower()).with_for_update())
    if existing is not None and existing.disabled_at is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "There's already an account for this email. Sign in instead.")
    if existing is not None:
        # A previously removed parent coming back: start fresh, with no old password, 2FA or passkeys.
        user = existing
        user.disabled_at = None
        user.totp_enabled = False
        user.totp_secret = None
        user.totp_last_counter = None
        user.failed_logins = 0
        user.locked_until = None
    else:
        user = AdminUser(id=uuid.uuid4(), email=inv.email)
        db.add(user)
    user.password_hash = hash_password(body.password)
    user.display_name = body.display_name.strip()
    inv.accepted_at = utcnow()
    db.flush()
    start_session(db, request, response, settings, user, "active", method="password", verified=not settings.require_totp)
    log_admin(db, user, "admin.invite_accepted", f"{user.name} ({user.email}) joined as a parent")
    db.commit()
    return user
