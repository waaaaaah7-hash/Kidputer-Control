from app.security import verify_totp

from .conftest import PASSWORD, make_admin, totp_code

LOGIN = "/api/v1/auth/login"


def test_login_requires_totp_step(client):
    _, secret = make_admin()
    r = client.post(LOGIN, json={"email": "Parent@Example.com", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["mfa_required"] is True
    # Password alone does not grant access.
    assert client.get("/api/v1/devices").status_code == 401
    r = client.post("/api/v1/auth/login/mfa", json={"code": totp_code(secret)})
    assert r.status_code == 200
    assert client.get("/api/v1/devices").status_code == 200
    assert client.get("/api/v1/auth/me").json()["email"] == "parent@example.com"


def test_wrong_password_and_unknown_user_look_the_same(client):
    make_admin()
    a = client.post(LOGIN, json={"email": "parent@example.com", "password": "nope"})
    b = client.post(LOGIN, json={"email": "nobody@example.com", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_lockout_after_repeated_failures(client):
    make_admin()
    for _ in range(5):
        assert client.post(LOGIN, json={"email": "parent@example.com", "password": "nope"}).status_code == 401
    # Locked even with the right password.
    assert client.post(LOGIN, json={"email": "parent@example.com", "password": PASSWORD}).status_code == 429


def test_totp_code_cannot_be_replayed(client):
    _, secret = make_admin()
    code = totp_code(secret)
    client.post(LOGIN, json={"email": "parent@example.com", "password": PASSWORD})
    assert client.post("/api/v1/auth/login/mfa", json={"code": code}).status_code == 200
    client.post("/api/v1/auth/logout")
    client.post(LOGIN, json={"email": "parent@example.com", "password": PASSWORD})
    assert client.post("/api/v1/auth/login/mfa", json={"code": code}).status_code == 401


def test_new_admin_must_enroll_totp_before_using_dashboard(client):
    make_admin(totp=False)
    r = client.post(LOGIN, json={"email": "parent@example.com", "password": PASSWORD})
    assert r.json() == {"mfa_required": False, "totp_setup_required": True}
    assert client.get("/api/v1/devices").status_code == 403

    setup = client.post("/api/v1/auth/totp/setup").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/")
    assert client.post("/api/v1/auth/totp/enable", json={"code": "000000"}).status_code == 400
    r = client.post("/api/v1/auth/totp/enable", json={"code": totp_code(setup["secret"])})
    assert r.status_code == 200 and r.json()["totp_enabled"] is True
    assert client.get("/api/v1/devices").status_code == 200


def test_state_changing_requests_need_csrf_header(admin_client):
    admin_client.headers.pop("X-Kidputer-Control")
    r = admin_client.post("/api/v1/devices", json={"name": "x"})
    assert r.status_code == 403
    assert admin_client.get("/api/v1/devices").status_code == 200


def test_logout_revokes_session(admin_client):
    assert admin_client.post("/api/v1/auth/logout").status_code == 204
    assert admin_client.get("/api/v1/devices").status_code == 401


def test_change_password(admin_client):
    r = admin_client.post("/api/v1/auth/password", json={"current_password": "wrong", "new_password": "a much better passphrase"})
    assert r.status_code == 400
    r = admin_client.post("/api/v1/auth/password", json={"current_password": PASSWORD, "new_password": "a much better passphrase"})
    assert r.status_code == 204
    admin_client.post("/api/v1/auth/logout")
    assert admin_client.post(LOGIN, json={"email": "parent@example.com", "password": PASSWORD}).status_code == 401


def test_verify_totp_drift_window():
    import pyotp

    secret = pyotp.random_base32()
    now = 1_800_000_000
    totp = pyotp.TOTP(secret)
    assert verify_totp(secret, totp.at(now - 30), None, now) is not None
    assert verify_totp(secret, totp.at(now - 90), None, now) is None
    assert verify_totp(secret, "12345", None, now) is None
