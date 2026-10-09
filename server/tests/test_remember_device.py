from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import update

from app.db import SessionLocal
from app.deps import utcnow
from app.main import app
from app.models import RememberedDevice

from .auth_helpers import make_sessions_stale, sign_in
from .conftest import CSRF, PASSWORD, make_admin, totp_code

A = "/api/v1/auth"


def password_only(client, email="parent@example.com"):
    return client.post(f"{A}/login", json={"email": email, "password": PASSWORD}).json()


def test_remembered_device_skips_the_code(client):
    _, secret = make_admin()
    sign_in(client, secret, remember=True)
    assert "kc_device" in client.cookies
    client.post(f"{A}/logout")
    assert password_only(client) == {"mfa_required": False, "totp_setup_required": False}
    current = [s for s in client.get(f"{A}/sessions").json() if s["current"]][0]
    assert current["auth_method"] == "password_remembered" and current["remembered"] is True
    assert client.get("/api/v1/devices").status_code == 200


def test_without_remember_the_code_is_still_needed(client):
    _, secret = make_admin()
    sign_in(client, secret)
    assert "kc_device" not in client.cookies
    client.post(f"{A}/logout")
    assert password_only(client)["mfa_required"] is True


def test_remembered_sign_in_must_confirm_risky_actions(client):
    _, secret = make_admin()
    sign_in(client, secret, remember=True)
    dev = client.post("/api/v1/devices", json={"name": "PC"}).json()["device_id"]
    client.post(f"{A}/logout")
    password_only(client)
    r = client.delete(f"/api/v1/devices/{dev}")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "reauth_required"
    assert client.put(f"/api/v1/devices/{dev}/pin", json={"pin": "1234"}).status_code == 403
    assert client.post(f"/api/v1/devices/{dev}/installer", json={}).status_code == 403
    assert client.post(f"{A}/reverify", json={"code": "000000"}).status_code == 401
    assert client.post(f"{A}/reverify", json={"code": totp_code(secret, 1)}).status_code == 204
    assert client.delete(f"/api/v1/devices/{dev}").status_code == 204


def test_reverify_codes_cannot_be_replayed(client):
    _, secret = make_admin()
    sign_in(client, secret)
    make_sessions_stale()
    code = totp_code(secret, 1)
    assert client.post(f"{A}/reverify", json={"code": code}).status_code == 204
    make_sessions_stale()
    assert client.post(f"{A}/reverify", json={"code": code}).status_code == 401


def test_remembered_device_is_per_parent(client):
    _, secret = make_admin()
    _, other_secret = make_admin("other@example.com")
    sign_in(client, secret, remember=True)
    client.post(f"{A}/logout")
    assert password_only(client, "other@example.com")["mfa_required"] is True


def test_expired_remembered_device_is_ignored(client):
    _, secret = make_admin()
    sign_in(client, secret, remember=True)
    client.post(f"{A}/logout")
    with SessionLocal() as db:
        db.execute(update(RememberedDevice).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    assert password_only(client)["mfa_required"] is True


def test_forgetting_a_device_signs_it_out(client):
    _, secret = make_admin()
    with TestClient(app) as phone:
        phone.headers.update(CSRF)
        sign_in(phone, secret, remember=True)
        phone.post(f"{A}/logout")
        password_only(phone)  # remembered sign-in on the "phone"
        sign_in(client, secret, step=1)  # the parent, on another browser
        devices = client.get(f"{A}/remembered-devices").json()
        assert len(devices) == 1 and devices[0]["current"] is False
        assert client.delete(f"{A}/remembered-devices/{devices[0]['id']}").status_code == 204
        assert phone.get("/api/v1/devices").status_code == 401  # the phone's session ended
        assert password_only(phone)["mfa_required"] is True  # and it isn't remembered any more


def test_password_change_forgets_remembered_devices(client):
    _, secret = make_admin()
    sign_in(client, secret, remember=True)
    r = client.post(f"{A}/password", json={"current_password": PASSWORD, "new_password": "a brand new passphrase"})
    assert r.status_code == 204
    assert client.get(f"{A}/remembered-devices").json() == []
    client.post(f"{A}/logout")
    r = client.post(f"{A}/login", json={"email": "parent@example.com", "password": "a brand new passphrase"})
    assert r.json()["mfa_required"] is True


def test_signed_in_devices_list_and_sign_out(client):
    _, secret = make_admin()
    sign_in(client, secret)
    with TestClient(app) as other:
        other.headers.update(CSRF)
        sign_in(other, secret, step=1)
        sessions = client.get(f"{A}/sessions").json()
        assert len(sessions) == 2 and sum(s["current"] for s in sessions) == 1
        other_id = [s["id"] for s in sessions if not s["current"]][0]
        assert client.delete(f"{A}/sessions/{other_id}").status_code == 204
        assert other.get("/api/v1/devices").status_code == 401
    assert len(client.get(f"{A}/sessions").json()) == 1


def test_sign_out_everywhere_else(client):
    _, secret = make_admin()
    sign_in(client, secret)
    with TestClient(app) as other:
        other.headers.update(CSRF)
        sign_in(other, secret, step=1)
        assert client.post(f"{A}/sessions/sign-out-others").status_code == 204
        assert other.get("/api/v1/devices").status_code == 401
        assert client.get("/api/v1/devices").status_code == 200


def test_sessions_belong_to_their_owner(client):
    _, secret = make_admin()
    _, other_secret = make_admin("other@example.com")
    sign_in(client, secret)
    with TestClient(app) as other:
        other.headers.update(CSRF)
        sign_in(other, other_secret, email="other@example.com")
        mine = client.get(f"{A}/sessions").json()[0]["id"]
        assert other.delete(f"{A}/sessions/{mine}").status_code == 404
