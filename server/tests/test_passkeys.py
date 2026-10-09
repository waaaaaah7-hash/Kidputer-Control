import pytest
from fastapi.testclient import TestClient

from app.main import app

from .auth_helpers import make_sessions_stale, sign_in
from .conftest import CSRF, make_admin, totp_code
from .soft_authenticator import SoftAuthenticator

P = "/api/v1/auth/passkeys"


@pytest.fixture
def parent(client):
    """A signed-in parent (password + TOTP, so recently verified) and their TOTP secret."""
    _, secret = make_admin()
    sign_in(client, secret)
    return client, secret


def add_passkey(client, auth: SoftAuthenticator, name: str | None = None):
    opts = client.post(f"{P}/register/options").json()
    assert opts["options"]["authenticatorSelection"]["userVerification"] == "required"
    return client.post(f"{P}/register/verify", json={"challenge_id": opts["challenge_id"], "credential": auth.create(opts["options"]), "name": name})


def passkey_login(client, auth: SoftAuthenticator, **kw):
    opts = client.post(f"{P}/login/options").json()
    return client.post(f"{P}/login/verify", json={"challenge_id": opts["challenge_id"], "credential": auth.get(opts["options"], **kw)})


def fresh_client():
    c = TestClient(app)
    c.headers.update(CSRF)
    return c


def test_add_passkey_then_sign_in_with_it(parent):
    client, _ = parent
    auth = SoftAuthenticator()
    r = add_passkey(client, auth, "My phone")
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "My phone"
    assert [p["name"] for p in client.get(P).json()] == ["My phone"]

    with fresh_client() as other:
        r = passkey_login(other, auth)
        assert r.status_code == 200, r.text
        assert r.json()["mfa_required"] is False
        assert other.get("/api/v1/auth/me").json()["email"] == "parent@example.com"
        current = [s for s in other.get("/api/v1/auth/sessions").json() if s["current"]][0]
        assert current["auth_method"] == "passkey"
        # A passkey sign-in counts as a recent second factor: risky actions work straight away.
        dev = other.post("/api/v1/devices", json={"name": "PC"}).json()["device_id"]
        assert other.delete(f"/api/v1/devices/{dev}").status_code == 204
    assert client.get(P).json()[0]["last_used_at"] is not None


def test_passkey_without_user_verification_is_refused(parent):
    client, _ = parent
    auth = SoftAuthenticator()
    opts = client.post(f"{P}/register/options").json()
    r = client.post(f"{P}/register/verify", json={"challenge_id": opts["challenge_id"], "credential": auth.create(opts["options"], user_verified=False)})
    assert r.status_code == 400
    assert add_passkey(client, auth).status_code == 201
    with fresh_client() as other:
        assert passkey_login(other, auth, user_verified=False).status_code == 401
        assert other.get("/api/v1/auth/me").status_code == 401


def test_challenges_are_single_use_and_bound_to_the_site(parent):
    client, _ = parent
    auth = SoftAuthenticator()
    add_passkey(client, auth)
    with fresh_client() as other:
        opts = other.post(f"{P}/login/options").json()
        cred = auth.get(opts["options"])
        assert other.post(f"{P}/login/verify", json={"challenge_id": opts["challenge_id"], "credential": cred}).status_code == 200
        assert other.post(f"{P}/login/verify", json={"challenge_id": opts["challenge_id"], "credential": cred}).status_code == 400
        assert passkey_login(other, auth, origin="https://evil.example").status_code == 401


def test_unknown_or_cloned_passkeys_are_refused(parent):
    client, _ = parent
    auth = SoftAuthenticator()
    add_passkey(client, auth)
    with fresh_client() as other:
        assert passkey_login(other, SoftAuthenticator()).status_code == 401
        assert passkey_login(other, auth, counter_step=5).status_code == 200
        # The signature counter went backwards: a copied key, so refuse.
        assert passkey_login(other, auth, counter_step=-2).status_code == 401


def test_same_authenticator_cannot_be_added_twice(parent):
    client, _ = parent
    auth = SoftAuthenticator()
    assert add_passkey(client, auth).status_code == 201
    opts = client.post(f"{P}/register/options").json()
    assert opts["options"]["excludeCredentials"][0]["id"]
    r = client.post(f"{P}/register/verify", json={"challenge_id": opts["challenge_id"], "credential": auth.create(opts["options"])})
    assert r.status_code == 409


def test_adding_and_removing_passkeys_needs_a_recent_check(parent):
    client, secret = parent
    auth = SoftAuthenticator()
    pk = add_passkey(client, auth).json()
    make_sessions_stale()
    r = client.post(f"{P}/register/options")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "reauth_required"
    assert client.delete(f"{P}/{pk['id']}").status_code == 403
    # Confirm with the passkey itself, then the action goes through.
    opts = client.post(f"{P}/reverify/options").json()
    assert len(opts["options"]["allowCredentials"]) == 1
    assert client.post(f"{P}/reverify/verify", json={"challenge_id": opts["challenge_id"], "credential": auth.get(opts["options"])}).status_code == 204
    assert client.delete(f"{P}/{pk['id']}").status_code == 204
    assert client.get(P).json() == []


def test_rename_passkey(parent):
    client, _ = parent
    pk = add_passkey(client, SoftAuthenticator()).json()
    assert client.patch(f"{P}/{pk['id']}", json={"name": "Laptop"}).json()["name"] == "Laptop"


def test_passkeys_belong_to_their_owner(parent):
    client, secret = parent
    auth = SoftAuthenticator()
    add_passkey(client, auth)
    _, other_secret = make_admin("other@example.com")
    with fresh_client() as other:
        sign_in(other, other_secret, email="other@example.com")
        assert other.get(P).json() == []
        assert other.post(f"{P}/reverify/options").status_code == 409  # has none of their own
        make_sessions_stale()
        # The first parent's passkey can't confirm the second parent's session.
        other_auth = SoftAuthenticator()
        other.post("/api/v1/auth/reverify", json={"code": totp_code(other_secret, 1)})
        add_passkey(other, other_auth)
        opts = other.post(f"{P}/reverify/options").json()
        r = other.post(f"{P}/reverify/verify", json={"challenge_id": opts["challenge_id"], "credential": auth.get(opts["options"])})
        assert r.status_code == 401


def test_locked_account_cannot_use_passkey(parent):
    client, _ = parent
    auth = SoftAuthenticator()
    add_passkey(client, auth)
    with fresh_client() as other:
        for _ in range(5):
            other.post("/api/v1/auth/login", json={"email": "parent@example.com", "password": "wrong"})
        assert passkey_login(other, auth).status_code == 429
