"""Tests run against a real Postgres (the schema relies on JSONB, partial unique indexes, ON CONFLICT).

    export KC_TEST_DATABASE_URL=postgresql+psycopg://kc:kc@localhost:5432/kidputercontrol_test
    pytest
"""

import os
import time
from pathlib import Path

import pyotp
import pytest

TEST_DB = os.environ.get("KC_TEST_DATABASE_URL", "postgresql+psycopg://kc:kc@localhost:5432/kidputercontrol_test")
os.environ["KC_DATABASE_URL"] = TEST_DB
os.environ["KC_COOKIE_SECURE"] = "false"  # TestClient talks plain http

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminUser  # noqa: E402
from app.security import hash_password  # noqa: E402

SERVER_DIR = Path(__file__).resolve().parents[1]
CSRF = {"X-Kidputer-Control": "1"}
PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    cfg = Config(str(SERVER_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def _clean_db():
    yield
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client():
    with TestClient(app) as c:
        c.headers.update(CSRF)
        yield c


def make_admin(email: str = "parent@example.com", totp: bool = True) -> tuple[AdminUser, str | None]:
    secret = pyotp.random_base32() if totp else None
    with SessionLocal() as db:
        user = AdminUser(email=email, password_hash=hash_password(PASSWORD), totp_secret=secret, totp_enabled=totp)
        db.add(user)
        db.commit()
        return user, secret


def totp_code(secret: str, offset_steps: int = 0) -> str:
    return pyotp.TOTP(secret).at(time.time() + 30 * offset_steps)


@pytest.fixture
def admin_client(client):
    """A client signed in as a fully authenticated (password + TOTP) admin."""
    _, secret = make_admin()
    r = client.post("/api/v1/auth/login", json={"email": "parent@example.com", "password": PASSWORD})
    assert r.json()["mfa_required"] is True
    r = client.post("/api/v1/auth/login/mfa", json={"code": totp_code(secret)})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def enrolled_device(admin_client):
    """Returns (device_id, device_token). The token is used via a separate agent client."""
    r = admin_client.post("/api/v1/devices", json={"name": "Kid PC", "timezone": "Europe/London"})
    assert r.status_code == 201, r.text
    code = r.json()["enrollment_code"]
    r = admin_client.post("/api/v1/agent/enroll", json={"enrollment_code": code, "hostname": "KID-PC", "agent_version": "1.0.0"})
    assert r.status_code == 200, r.text
    return r.json()["device_id"], r.json()["device_token"]


@pytest.fixture
def agent(enrolled_device):
    device_id, token = enrolled_device
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {token}"
        c.device_id = device_id
        yield c
