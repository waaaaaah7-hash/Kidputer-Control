import hashlib
import hmac
import secrets
import time

import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_ph = PasswordHasher()  # argon2id with library defaults

# Pre-computed so a login for an unknown email still spends an argon2 verify (no user-enumeration timing).
_DUMMY_HASH = _ph.hash(secrets.token_urlsafe(16))

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _ph.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    """Opaque bearer secret (session cookie, device token). Only its SHA-256 is stored."""
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> bytes:
    # Tokens are 256-bit random values, so a fast hash is sufficient for at-rest storage.
    return hashlib.sha256(token.encode()).digest()


# Enrollment codes are typed by hand on the child's PC: unambiguous alphabet, 12 chars (~60 bits), grouped.
_ENROLL_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"


def new_enrollment_code() -> str:
    raw = "".join(secrets.choice(_ENROLL_ALPHABET) for _ in range(12))
    return "-".join(raw[i : i + 4] for i in range(0, 12, 4))


def normalize_enrollment_code(code: str) -> str:
    raw = "".join(ch for ch in code.upper() if ch.isalnum())
    return "-".join(raw[i : i + 4] for i in range(0, len(raw), 4))


# --- TOTP -------------------------------------------------------------------


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_uri(secret: str, email: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer)


def verify_totp(secret: str, code: str, last_counter: int | None, now: float | None = None) -> int | None:
    """Return the matched time-step counter, or None. Accepts +/-1 step of clock drift and rejects replays."""
    code = code.strip().replace(" ", "")
    if not code.isdigit() or len(code) != 6:
        return None
    totp = pyotp.TOTP(secret)
    now = time.time() if now is None else now
    current = int(now // totp.interval)
    for counter in (current - 1, current, current + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(totp.generate_otp(counter), code):
            return counter
    return None


# --- Lock-screen PIN ----------------------------------------------------------
# PBKDF2-HMAC-SHA256 so the agent can verify offline with .NET:
#   [Security.Cryptography.Rfc2898DeriveBytes]::new($pin, $salt, $iterations, 'SHA256').GetBytes(32)


def hash_pin(pin: str, iterations: int) -> tuple[bytes, bytes]:
    salt = secrets.token_bytes(16)
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations, dklen=32), salt


def verify_pin(pin: str, pin_hash: bytes, salt: bytes, iterations: int) -> bool:
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, iterations, dklen=32), pin_hash)
