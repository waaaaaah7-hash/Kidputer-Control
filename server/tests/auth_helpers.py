from datetime import timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.deps import utcnow
from app.models import AdminSession

from .conftest import PASSWORD, totp_code

EMAIL = "parent@example.com"


def sign_in(client, secret: str, *, remember: bool = False, step: int = 0, email: str = EMAIL):
    """Password + TOTP sign-in. `step` picks a later 30 s code, since each code works only once."""
    r = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    if r.json()["mfa_required"]:
        r = client.post("/api/v1/auth/login/mfa", json={"code": totp_code(secret, step), "remember": remember})
        assert r.status_code == 200, r.text
    return r


def make_sessions_stale() -> None:
    """Pretend the last second-factor check was an hour ago, so risky actions ask again."""
    with SessionLocal() as db:
        db.execute(update(AdminSession).values(verified_at=utcnow() - timedelta(hours=1)))
        db.commit()
