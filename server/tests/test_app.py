import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bootstrap import bootstrap_admin
from app.config import Settings
from app.db import SessionLocal
from app.models import ActivityEvent, AdminUser

from .conftest import make_admin


def test_render_style_database_url_gets_driver():
    assert Settings(database_url="postgres://u:p@h:5432/db").database_url == "postgresql+psycopg://u:p@h:5432/db"
    assert Settings(database_url="postgresql://u:p@h/db").database_url == "postgresql+psycopg://u:p@h/db"


def test_bootstrap_creates_first_admin_only_once():
    s = Settings(bootstrap_admin_email="first@example.com", bootstrap_admin_password="a long enough password")
    assert bootstrap_admin(s) is True
    assert bootstrap_admin(s) is False
    with SessionLocal() as db:
        assert db.scalars(select(AdminUser.email)).all() == ["first@example.com"]
        assert db.scalar(select(ActivityEvent.event_type)) == "admin.bootstrapped"


def test_bootstrap_skips_when_admins_exist_or_password_short():
    assert bootstrap_admin(Settings(bootstrap_admin_email="x@example.com", bootstrap_admin_password="short")) is False
    make_admin()
    assert bootstrap_admin(Settings(bootstrap_admin_email="x@example.com", bootstrap_admin_password="a long enough password")) is False


def test_api_responses_are_not_cached(client):
    r = client.get("/api/v1/devices")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-frame-options"] == "DENY"


@pytest.fixture
def spa_client(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>shell</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    monkeypatch.setenv("SM_STATIC_DIR", str(tmp_path))
    from app import config, main

    config.get_settings.cache_clear()
    reloaded = importlib.reload(main)
    with TestClient(reloaded.app) as c:
        yield c
    monkeypatch.delenv("SM_STATIC_DIR")
    config.get_settings.cache_clear()
    importlib.reload(main)


def test_spa_is_served_with_client_side_routes(spa_client):
    r = spa_client.get("/devices/123")
    assert r.status_code == 200 and "shell" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert spa_client.get("/assets/app.js").text == "console.log(1)"
    assert spa_client.get("/favicon.svg").text == "<svg/>"
    assert "shell" in spa_client.get("/../../etc/passwd").text
    assert spa_client.get("/api/v1/nope").status_code == 404
    assert spa_client.get("/healthz").json() == {"status": "ok"}
