"""Passkeys (WebAuthn): sign in with Face ID, a fingerprint, or Windows Hello instead of password + code.

A passkey counts as multi-factor on its own only when the authenticator verified the user (biometric or
device PIN), so every ceremony here requires user verification and rejects responses without it.
"""

import json
import uuid
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..audit import log_admin
from ..deps import DbDep, SettingsDep, VerifiedAdminDep, active_session, current_admin, utcnow
from ..models import AdminSession, AdminUser, Passkey, WebAuthnChallenge
from ..schemas import LoginResponse, PasskeyOptionsOut, PasskeyOut, PasskeyRename, PasskeyVerifyRequest
from .auth import is_locked, start_session

router = APIRouter(prefix="/auth/passkeys", tags=["passkeys"])

_CHALLENGE_TTL = timedelta(minutes=5)
_NOT_RECOGNISED = HTTPException(status.HTTP_401_UNAUTHORIZED, "That passkey wasn't accepted. Try again, or sign in with your password.")


def _relying_party(request: Request) -> tuple[str, str]:
    """(rp_id, origin) for the address the dashboard was opened on. Passkeys are bound to this domain."""
    host = request.url.hostname or "localhost"
    return host, f"{request.url.scheme}://{request.url.netloc}"


def _new_challenge(db: Session, purpose: str, user_id: uuid.UUID | None, challenge: bytes) -> uuid.UUID:
    now = utcnow()
    db.execute(delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at < now))
    row = WebAuthnChallenge(id=uuid.uuid4(), challenge=challenge, purpose=purpose, user_id=user_id, expires_at=now + _CHALLENGE_TTL)
    db.add(row)
    return row.id


def _take_challenge(db: Session, challenge_id: uuid.UUID, purpose: str, user_id: uuid.UUID | None) -> bytes:
    """Fetch and delete a challenge (single use)."""
    row = db.get(WebAuthnChallenge, challenge_id)
    if row is not None:
        # Committed straight away, so a challenge can't be retried even if verification then fails.
        db.delete(row)
        db.commit()
    if row is None or row.purpose != purpose or row.user_id != user_id or row.expires_at <= utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This passkey request expired. Please try again.")
    return row.challenge


def _descriptors(passkeys: list[Passkey]) -> list[PublicKeyCredentialDescriptor]:
    out = []
    for p in passkeys:
        transports = []
        for t in p.transports or []:
            try:
                transports.append(AuthenticatorTransport(t))
            except ValueError:
                pass
        out.append(PublicKeyCredentialDescriptor(id=p.credential_id, transports=transports or None))
    return out


def _credential_id(credential: dict[str, Any]) -> bytes:
    try:
        return base64url_to_bytes(str(credential.get("rawId") or credential.get("id") or ""))
    except Exception as exc:  # malformed input
        raise _NOT_RECOGNISED from exc


def _default_name(request: Request) -> str:
    ua = request.headers.get("user-agent") or ""
    for needle, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android phone"), ("Windows", "Windows PC"), ("Macintosh", "Mac"), ("CrOS", "Chromebook")):
        if needle in ua:
            return f"Passkey on {name}"
    return "Passkey"


def _options_out(challenge_id: uuid.UUID, options: Any) -> PasskeyOptionsOut:
    return PasskeyOptionsOut(challenge_id=challenge_id, options=json.loads(options_to_json(options)))


def _verify_assertion(db: Session, request: Request, body: PasskeyVerifyRequest, challenge: bytes, user_id: uuid.UUID | None) -> Passkey:
    """Check a passkey assertion; returns the passkey used (sign count and last use updated)."""
    passkey = db.scalar(select(Passkey).where(Passkey.credential_id == _credential_id(body.credential)))
    if passkey is None or (user_id is not None and passkey.user_id != user_id):
        raise _NOT_RECOGNISED
    rp_id, origin = _relying_party(request)
    try:
        verified = verify_authentication_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as exc:
        raise _NOT_RECOGNISED from exc
    passkey.sign_count = verified.new_sign_count
    passkey.backed_up = verified.credential_backed_up
    passkey.last_used_at = utcnow()
    return passkey


# --- Sign in --------------------------------------------------------------------------------------------


@router.post("/login/options", response_model=PasskeyOptionsOut)
def login_options(request: Request, db: DbDep) -> PasskeyOptionsOut:
    rp_id, _ = _relying_party(request)
    # No allow-list: the browser offers whichever passkey for this site the parent has (discoverable).
    options = generate_authentication_options(rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED)
    challenge_id = _new_challenge(db, "login", None, options.challenge)
    db.commit()
    return _options_out(challenge_id, options)


@router.post("/login/verify", response_model=LoginResponse)
def login_verify(body: PasskeyVerifyRequest, request: Request, response: Response, db: DbDep, settings: SettingsDep) -> LoginResponse:
    challenge = _take_challenge(db, body.challenge_id, "login", None)
    passkey = _verify_assertion(db, request, body, challenge, None)
    user = db.get(AdminUser, passkey.user_id, with_for_update=True)
    if is_locked(user):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "account temporarily locked; try again later")
    user.failed_logins = 0
    start_session(db, request, response, settings, user, "active", method="passkey", verified=True)
    log_admin(db, user, "admin.login", f"{user.email} signed in with passkey '{passkey.name}'")
    db.commit()
    return LoginResponse(mfa_required=False)


# --- Confirm it's you (before risky actions) -------------------------------------------------------------


@router.post("/reverify/options", response_model=PasskeyOptionsOut)
def reverify_options(
    request: Request, db: DbDep, user: Annotated[AdminUser, Depends(current_admin)]
) -> PasskeyOptionsOut:
    passkeys = list(db.scalars(select(Passkey).where(Passkey.user_id == user.id)))
    if not passkeys:
        raise HTTPException(status.HTTP_409_CONFLICT, "You don't have a passkey yet.")
    rp_id, _ = _relying_party(request)
    options = generate_authentication_options(
        rp_id=rp_id, allow_credentials=_descriptors(passkeys), user_verification=UserVerificationRequirement.REQUIRED
    )
    challenge_id = _new_challenge(db, "reverify", user.id, options.challenge)
    db.commit()
    return _options_out(challenge_id, options)


@router.post("/reverify/verify", status_code=status.HTTP_204_NO_CONTENT)
def reverify_verify(
    body: PasskeyVerifyRequest,
    request: Request,
    db: DbDep,
    sess: Annotated[AdminSession, Depends(active_session)],
    user: Annotated[AdminUser, Depends(current_admin)],
) -> None:
    challenge = _take_challenge(db, body.challenge_id, "reverify", user.id)
    _verify_assertion(db, request, body, challenge, user.id)
    sess.verified_at = utcnow()
    db.commit()


# --- Manage passkeys --------------------------------------------------------------------------------------


@router.get("", response_model=list[PasskeyOut])
def list_passkeys(db: DbDep, user: Annotated[AdminUser, Depends(current_admin)]) -> list[Passkey]:
    return list(db.scalars(select(Passkey).where(Passkey.user_id == user.id).order_by(Passkey.created_at)))


@router.post("/register/options", response_model=PasskeyOptionsOut)
def register_options(request: Request, db: DbDep, settings: SettingsDep, user: VerifiedAdminDep) -> PasskeyOptionsOut:
    existing = list(db.scalars(select(Passkey).where(Passkey.user_id == user.id)))
    rp_id, _ = _relying_party(request)
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.email,
        exclude_credentials=_descriptors(existing),  # don't register the same authenticator twice
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,  # discoverable: sign in without typing the email
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    challenge_id = _new_challenge(db, "register", user.id, options.challenge)
    db.commit()
    return _options_out(challenge_id, options)


@router.post("/register/verify", response_model=PasskeyOut, status_code=status.HTTP_201_CREATED)
def register_verify(body: PasskeyVerifyRequest, request: Request, db: DbDep, user: VerifiedAdminDep) -> Passkey:
    challenge = _take_challenge(db, body.challenge_id, "register", user.id)
    rp_id, origin = _relying_party(request)
    try:
        verified = verify_registration_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The passkey couldn't be added. Please try again.") from exc
    if db.scalar(select(Passkey).where(Passkey.credential_id == verified.credential_id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "That passkey is already added.")
    transports = body.credential.get("response", {}).get("transports") if isinstance(body.credential.get("response"), dict) else None
    passkey = Passkey(
        id=uuid.uuid4(),
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=[str(t) for t in transports][:8] if isinstance(transports, list) else None,
        backed_up=verified.credential_backed_up,
        name=(body.name or "").strip() or _default_name(request),
        created_at=utcnow(),
    )
    db.add(passkey)
    log_admin(db, user, "admin.passkey_added", f"{user.email} added passkey '{passkey.name}'")
    db.commit()
    return passkey


@router.patch("/{passkey_id}", response_model=PasskeyOut)
def rename_passkey(passkey_id: uuid.UUID, body: PasskeyRename, db: DbDep, user: Annotated[AdminUser, Depends(current_admin)]) -> Passkey:
    passkey = db.get(Passkey, passkey_id)
    if passkey is None or passkey.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "passkey not found")
    passkey.name = body.name.strip()
    db.commit()
    return passkey


@router.delete("/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_passkey(passkey_id: uuid.UUID, db: DbDep, user: VerifiedAdminDep) -> None:
    passkey = db.get(Passkey, passkey_id)
    if passkey is None or passkey.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "passkey not found")
    log_admin(db, user, "admin.passkey_removed", f"{user.email} removed passkey '{passkey.name}'")
    db.delete(passkey)
    db.commit()
