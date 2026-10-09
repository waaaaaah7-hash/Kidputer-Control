"""Several parents: invite by one-time link, accept, remove, and names in the activity log."""

from datetime import timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.deps import utcnow
from app.models import AdminInvite, AdminUser

from .auth_helpers import make_sessions_stale, sign_in
from .conftest import PASSWORD, make_admin, totp_code

A = "/api/v1/auth"
P = "/api/v1/parents"
SECOND = "partner@example.com"
NEW_PASSWORD = "another long passphrase"


def token_of(link: str) -> str:
    assert "/invite#" in link
    return link.split("#", 1)[1]


def invite(client, email: str = SECOND) -> str:
    r = client.post(f"{P}/invites", json={"email": email})
    assert r.status_code == 201, r.text
    return token_of(r.json()["link"])


def accept(client, token: str, name: str = "Sam", password: str = NEW_PASSWORD):
    return client.post("/api/v1/invites/accept", json={"token": token, "display_name": name, "password": password})


def test_creating_an_invite_needs_a_recent_check(client):
    _, secret = make_admin()
    sign_in(client, secret)
    make_sessions_stale()
    r = client.post(f"{P}/invites", json={"email": SECOND})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "reauth_required"
    assert client.post(f"{A}/reverify", json={"code": totp_code(secret, 1)}).status_code == 204
    assert client.post(f"{P}/invites", json={"email": SECOND}).status_code == 201


def test_full_invite_flow(client, other_client):
    _, secret = make_admin()
    sign_in(client, secret)
    client.patch(f"{A}/me", json={"display_name": "Alex"})
    r = client.post(f"{P}/invites", json={"email": SECOND})
    link = r.json()["link"]
    assert link.startswith("http://testserver/invite#")
    token = token_of(link)
    pending = client.get(f"{P}/invites").json()
    assert [i["email"] for i in pending] == [SECOND] and pending[0]["invited_by"] == "Alex"

    info = other_client.post("/api/v1/invites/lookup", json={"token": token})
    assert info.status_code == 200 and info.json()["email"] == SECOND and info.json()["invited_by"] == "Alex"

    r = accept(other_client, token)
    assert r.status_code == 200, r.text
    assert r.json()["email"] == SECOND and r.json()["name"] == "Sam" and r.json()["totp_enabled"] is False
    # Signed in, but nothing works until they set up their own two-factor sign-in.
    assert other_client.get(f"{A}/me").status_code == 200
    assert other_client.get("/api/v1/devices").status_code == 403
    assert other_client.post(f"{A}/totp/setup").status_code == 200

    parents = client.get(P).json()
    assert [(p["email"], p["name"], p["you"]) for p in parents] == [
        ("parent@example.com", "Alex", True),
        (SECOND, "Sam", False),
    ]
    assert client.get(f"{P}/invites").json() == []
    actions = [e["event_type"] for e in client.get("/api/v1/events").json()]
    assert "admin.invite_created" in actions and "admin.invite_accepted" in actions


def test_invite_links_work_once(client, other_client):
    _, secret = make_admin()
    sign_in(client, secret)
    token = invite(client)
    assert accept(other_client, token).status_code == 200
    other_client.cookies.clear()
    assert accept(other_client, token, password="yet another passphrase").status_code == 410
    assert other_client.post("/api/v1/invites/lookup", json={"token": token}).status_code == 410


def test_expired_cancelled_and_replaced_invites_are_gone(client, other_client):
    _, secret = make_admin()
    sign_in(client, secret)
    expired = invite(client, "a@example.com")
    with SessionLocal() as db:
        db.execute(update(AdminInvite).values(expires_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    assert accept(other_client, expired).status_code == 410

    cancelled = invite(client, "b@example.com")
    inv_id = client.get(f"{P}/invites").json()[0]["id"]
    assert client.delete(f"{P}/invites/{inv_id}").status_code == 204
    assert accept(other_client, cancelled).status_code == 410

    first = invite(client, "c@example.com")
    second = invite(client, "C@example.com")  # a new invite replaces the old one
    assert accept(other_client, first).status_code == 410
    assert accept(other_client, second).status_code == 200


def test_bad_tokens_are_gone(other_client):
    assert other_client.post("/api/v1/invites/lookup", json={"token": "x" * 43}).status_code == 410
    assert accept(other_client, "x" * 43).status_code == 410


def test_accepting_needs_a_long_password(client, other_client):
    _, secret = make_admin()
    sign_in(client, secret)
    token = invite(client)
    assert accept(other_client, token, password="short").status_code == 422
    assert accept(other_client, token, name=" ").status_code == 422
    assert accept(other_client, token).status_code == 200


def test_cannot_invite_an_existing_parent(client):
    _, secret = make_admin()
    sign_in(client, secret)
    r = client.post(f"{P}/invites", json={"email": "PARENT@example.com"})
    assert r.status_code == 409


def test_removing_a_parent(client, other_client):
    _, secret = make_admin()
    sign_in(client, secret)
    _, secret2 = make_admin(SECOND)
    sign_in(other_client, secret2, email=SECOND, remember=True)
    second_id = next(p["id"] for p in client.get(P).json() if p["email"] == SECOND)

    make_sessions_stale()
    r = client.delete(f"{P}/{second_id}")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "reauth_required"
    assert client.post(f"{A}/reverify", json={"code": totp_code(secret, 1)}).status_code == 204
    assert client.delete(f"{P}/{second_id}").status_code == 204

    # Signed out everywhere, and can't sign back in, even from a remembered browser.
    assert other_client.get(f"{A}/me").status_code == 401
    r = other_client.post(f"{A}/login", json={"email": SECOND, "password": PASSWORD})
    assert r.status_code == 401
    assert [p["email"] for p in client.get(P).json()] == ["parent@example.com"]
    assert client.delete(f"{P}/{second_id}").status_code == 404
    assert "admin.parent_removed" in [e["event_type"] for e in client.get("/api/v1/events").json()]


def test_cannot_remove_yourself(client):
    user, secret = make_admin()
    sign_in(client, secret)
    r = client.delete(f"{P}/{user.id}")
    assert r.status_code == 400 and "yourself" in r.json()["detail"]


def test_removed_parent_can_be_invited_back_with_fresh_sign_in(client, other_client):
    _, secret = make_admin()
    sign_in(client, secret)
    second, _ = make_admin(SECOND)
    assert client.delete(f"{P}/{second.id}").status_code == 204
    token = invite(client)
    r = accept(other_client, token, name="Sam again")
    assert r.status_code == 200 and r.json()["totp_enabled"] is False
    with SessionLocal() as db:
        back = db.get(AdminUser, second.id)
        assert back.disabled_at is None and back.totp_secret is None and back.display_name == "Sam again"
    # The old password no longer works; the new one does.
    other_client.cookies.clear()
    assert other_client.post(f"{A}/login", json={"email": SECOND, "password": PASSWORD}).status_code == 401
    assert other_client.post(f"{A}/login", json={"email": SECOND, "password": NEW_PASSWORD}).status_code == 200


def test_activity_log_names_who_did_it(client, other_client):
    _, secret = make_admin()
    sign_in(client, secret)
    client.patch(f"{A}/me", json={"display_name": "Alex"})
    _, secret2 = make_admin(SECOND)
    sign_in(other_client, secret2, email=SECOND)
    dev = client.post("/api/v1/devices", json={"name": "Kid PC"}).json()["device_id"]
    other_client.post(f"/api/v1/devices/{dev}/message", json={"body": "Dinner!", "mode": "closable"})
    events = client.get("/api/v1/events").json()
    names = {e["event_type"]: e["actor_name"] for e in events}
    assert names["device.created"] == "Alex"
    assert any(e["actor_name"] == "partner" for e in events)  # no name set: the part of the email before @


def test_display_name_rules(client):
    _, secret = make_admin()
    sign_in(client, secret)
    assert client.get(f"{A}/me").json()["name"] == "parent"
    r = client.patch(f"{A}/me", json={"display_name": "  Alex  "})
    assert r.status_code == 200 and r.json()["name"] == "Alex"
    assert client.patch(f"{A}/me", json={"display_name": "x" * 61}).status_code == 422


def test_parent_endpoints_need_sign_in(other_client):
    assert other_client.get(P).status_code == 401
    assert other_client.post(f"{P}/invites", json={"email": SECOND}).status_code == 401


def test_parent_changes_need_the_csrf_header(client):
    _, secret = make_admin()
    sign_in(client, secret)
    del client.headers["X-Kidputer-Control"]
    assert client.post(f"{P}/invites", json={"email": SECOND}).status_code == 403
