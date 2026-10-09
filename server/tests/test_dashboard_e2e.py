"""End-to-end: drive the built dashboard (web/dist) in a real browser against a live API.

Skipped unless Playwright is installed and the dashboard has been built (cd web && npm run build).
KC_CHROMIUM_PATH points Playwright at an existing Chromium; KC_E2E_SCREENSHOTS=<dir> saves screenshots.
"""

import importlib
import json
import os
import re
import socket
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pyotp
import pytest
import uvicorn

from .conftest import PASSWORD, make_admin

playwright_api = pytest.importorskip("playwright.sync_api")
DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
pytestmark = pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="dashboard not built")


@pytest.fixture
def live_app(monkeypatch):
    monkeypatch.setenv("KC_STATIC_DIR", str(DIST))
    from app import config, main

    config.get_settings.cache_clear()
    app = importlib.reload(main).app
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline
        time.sleep(0.05)
    # "localhost", not an IP address: passkeys (WebAuthn) refuse IP addresses as the site identity.
    yield f"http://localhost:{port}"
    server.should_exit = True
    thread.join(timeout=5)
    monkeypatch.delenv("KC_STATIC_DIR")
    config.get_settings.cache_clear()
    importlib.reload(main)


@pytest.fixture
def page(live_app):
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("KC_CHROMIUM_PATH") or None)
        context = browser.new_context(base_url=live_app, viewport={"width": 1280, "height": 900})
        pg = context.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        # 401 (signed out) and 403 (asked to confirm it's you) responses are expected; anything else is a bug.
        pg.on("console", lambda m: m.type == "error" and not EXPECTED_HTTP_ERROR.search(m.text) and errors.append(m.text))
        yield pg
        browser.close()
        assert not errors, errors


EXPECTED_HTTP_ERROR = re.compile(r"status of 40[13]")


def assert_fits_screen(page) -> None:
    """Nothing sticks out of the screen, or out of the card it sits in."""
    too_wide = page.evaluate(
        """[...document.querySelectorAll('body *')].filter(e => {
             const r = e.getBoundingClientRect().right, card = e.closest('.card');
             return r > window.innerWidth + 1 || (card && card !== e && r > card.getBoundingClientRect().right + 1);
           })
           .map(e => e.tagName + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right)).slice(0, 8)"""
    )
    assert not too_wide, f"content overflows on phone: {too_wide}"


def shot(page, name: str) -> None:
    out = os.environ.get("KC_E2E_SCREENSHOTS")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(out) / f"{name}.png"), full_page=True)


def expect(locator):
    return playwright_api.expect(locator)


def test_parent_journey(page, live_app):
    make_admin(totp=False)

    # Sign in, then mandatory 2FA enrollment.
    page.goto("/")
    expect(page).to_have_url(re.compile(r"/login$"))
    page.get_by_label("Email").fill("parent@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    shot(page, "01-login")
    page.get_by_role("button", name="Continue").click()
    expect(page).to_have_url(re.compile(r"/setup-2fa$"))
    expect(page.get_by_role("img", name=re.compile("QR code"))).to_be_visible()
    secret = page.locator("code.secret").inner_text().replace(" ", "")
    shot(page, "02-totp-setup")
    page.get_by_label("Code from the app").fill(pyotp.TOTP(secret).now())
    page.get_by_role("button", name="Turn on two-factor sign-in").click()
    expect(page.get_by_role("heading", name="Devices")).to_be_visible()
    expect(page.get_by_text("No PCs yet")).to_be_visible()

    # Add a PC and download its one-click installer.
    page.get_by_role("button", name="Add a PC").click()
    page.get_by_label("Name", exact=True).fill("Sam's PC")
    page.get_by_role("button", name="Create").click()
    shown_code = page.get_by_test_id("enrollment-code").text_content()
    page.get_by_label("Child's Windows account").fill("Sam")
    with page.expect_download() as dl:
        page.get_by_role("button", name="Download installer").click()
    download = dl.value
    assert download.suggested_filename == "KidputerControl-Installer.zip"
    with zipfile.ZipFile(download.path()) as z:
        config = json.loads(z.read("KidputerControl-Installer/install-config.json"))
        assert "KidputerControl-Installer/Install Kidputer Control.cmd" in z.namelist()
    assert config["child_user"] == "Sam" and config["api_url"] == live_app
    code = config["enrollment_code"]
    assert re.fullmatch(r"[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}", code) and code != shown_code
    expect(page.locator(".steps-done")).to_be_visible()
    # The by-hand instructions now show the installer's code, not the superseded one.
    page.get_by_text("Install by hand instead").click()
    expect(page.locator("pre.command")).to_contain_text(f"-EnrollmentCode {code}")
    shot(page, "03-enrollment")
    page.get_by_role("link", name="Set the schedule").click()
    expect(page.get_by_role("heading", name="Sam's PC")).to_be_visible()

    # The agent installs and reports it is locked for the night.
    agent = httpx.Client(base_url=live_app)
    token = agent.post("/api/v1/agent/enroll", json={"enrollment_code": code, "hostname": "SAM-PC", "agent_version": "1.0.0"}).json()["device_token"]
    agent.headers["Authorization"] = f"Bearer {token}"
    agent.post("/api/v1/agent/heartbeat", json={"status": {"mode": "locked", "reason": "schedule", "unlock_at": "2030-01-01T07:00:00Z", "ui_agent_alive": True, "console_user_is_child": True}})
    page.reload()
    expect(page.locator(".device-status .badge")).to_have_text("Locked")
    expect(page.locator(".device-head")).to_contain_text("SAM-PC · agent 1.0.0")

    # Lock now (immediate lock) with a reason.
    page.get_by_placeholder("Reason (optional").fill("Homework first")
    page.get_by_role("button", name="Lock now").click()
    expect(page.get_by_text("Locked by a parent")).to_be_visible()
    assert agent.get("/api/v1/agent/state").json()["immediate_lock"]["reason"] == "Homework first"

    # Extra time.
    page.get_by_role("button", name="+30 min").click()
    expect(page.get_by_role("button", name="Cancel")).to_be_visible()
    assert len(agent.get("/api/v1/agent/state").json()["overrides"]) == 1

    # Persistent message.
    page.get_by_label("Send a message").fill("Dinner in 10 minutes")
    page.get_by_label("Stays until I remove it").check()
    page.get_by_role("button", name="Send", exact=True).click()
    expect(page.locator(".message-body")).to_have_text("Dinner in 10 minutes")
    assert agent.get("/api/v1/agent/state").json()["message"]["mode"] == "persistent"

    # Schedule: Monday 21:30-07:00, copied to Tue-Fri.
    page.get_by_role("checkbox", name="Monday").check()
    page.get_by_label("Monday lock time").fill("21:30")
    page.get_by_label("Monday unlock time").fill("07:00")
    page.get_by_role("button", name="Copy Monday to Tue–Fri").click()
    expect(page.get_by_text("until 07:00 next day · 9 h 30 min").first).to_be_visible()
    page.get_by_role("button", name="Save schedule").click()
    expect(page.get_by_text("Schedule saved")).to_be_visible()
    schedule = agent.get("/api/v1/agent/state").json()["schedule"]
    assert [e["day_of_week"] for e in schedule] == [0, 1, 2, 3, 4]
    assert schedule[0] == {"day_of_week": 0, "lock_at": "21:30", "unlock_at": "07:00"}

    # Settings and PIN.
    page.get_by_label("Countdown before locking (minutes)").fill("2")
    page.get_by_role("button", name="Save settings").click()
    expect(page.get_by_text("Settings saved")).to_be_visible()
    assert agent.get("/api/v1/agent/state").json()["countdown_seconds"] == 120
    page.get_by_label("New PIN").fill("4321")
    page.get_by_label("Repeat PIN").fill("4321")
    page.get_by_role("button", name="Set PIN").click()
    expect(page.get_by_role("button", name="Change PIN")).to_be_visible()
    assert agent.get("/api/v1/agent/state").json()["pin"]["hash"]
    shot(page, "04-device")

    # End the immediate lock again.
    page.get_by_role("button", name="End immediate lock").click()
    expect(page.get_by_role("button", name="Lock now")).to_be_visible()

    # Tamper alert from the agent shows on the devices overview and in the activity log.
    agent.post("/api/v1/agent/heartbeat", json={"status": {"mode": "none", "reason": "none", "tamper_problems": ["modified: Watchdog.ps1"]}})
    agent.post("/api/v1/agent/events", json={"events": [{"client_event_id": "6a1c9a9e-3c1a-4b6e-9f70-9d8d3cf0b001", "event_type": "tamper.detected", "severity": "critical", "message": "modified: Watchdog.ps1", "occurred_at": "2026-01-01T00:00:00Z"}]})
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Devices").click()
    expect(page.locator(".device-card .badge")).to_have_text("Tamper alert")
    shot(page, "05-devices")
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Activity").click()
    expect(page.get_by_text("Immediate lock requested")).to_be_visible()
    page.get_by_label("Importance").select_option("critical")
    expect(page.locator(".event")).to_have_count(1)
    expect(page.get_by_text("Tamper detected")).to_be_visible()
    shot(page, "06-activity")

    # Phone-sized layout.
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Devices").click()
    page.locator(".device-card").click()
    expect(page.get_by_role("button", name="Lock now")).to_be_visible()
    assert_fits_screen(page)
    shot(page, "07-phone")

    # Sign out.
    page.get_by_role("button", name="Sign out").click()
    expect(page).to_have_url(re.compile(r"/login$"))
    page.goto("/activity")
    expect(page).to_have_url(re.compile(r"/login$"))


def add_virtual_authenticator(page) -> None:
    """Chromium's built-in test authenticator: a platform passkey with a fingerprint/PIN that always passes."""
    cdp = page.context.new_cdp_session(page)
    cdp.send("WebAuthn.enable", {"enableUI": False})
    cdp.send(
        "WebAuthn.addVirtualAuthenticator",
        {
            "options": {
                "protocol": "ctap2",
                "transport": "internal",
                "hasResidentKey": True,
                "hasUserVerification": True,
                "isUserVerified": True,
                "automaticPresenceSimulation": True,
            }
        },
    )


def test_passkeys_and_remembered_device(page, live_app):
    _, secret = make_admin()
    add_virtual_authenticator(page)
    page.on("dialog", lambda d: d.accept())  # "Are you sure?" confirmations

    # Sign in with password + code, ticking "Remember this device".
    page.goto("/login")
    page.get_by_label("Email").fill("parent@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    page.get_by_label("Authentication code").fill(pyotp.TOTP(secret).now())
    page.get_by_label("Remember this device for 30 days").check()
    page.get_by_role("button", name="Verify").click()
    expect(page.get_by_role("heading", name="Devices", exact=True)).to_be_visible()

    # Add a passkey on the Account page.
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Account").click()
    expect(page.get_by_text("You haven't added a passkey yet.")).to_be_visible()
    page.get_by_label("Passkey name").fill("Test phone")
    page.get_by_role("button", name="Add a passkey").click()
    expect(page.get_by_test_id("passkey-list")).to_contain_text("Test phone")
    expect(page.get_by_test_id("session-list")).to_contain_text("This device")
    expect(page.get_by_test_id("remembered-list")).to_contain_text("This browser")
    shot(page, "08-account")
    page.set_viewport_size({"width": 390, "height": 844})
    assert_fits_screen(page)
    shot(page, "08-account-phone")
    page.set_viewport_size({"width": 1280, "height": 900})

    # Sign out, then sign back in with just the passkey.
    page.get_by_role("button", name="Sign out").first.click()
    expect(page).to_have_url(re.compile(r"/login$"))
    shot(page, "09-login-passkey")
    page.get_by_role("button", name="Sign in with a passkey").click()
    # Signing in returns to the page you were on (Account).
    expect(page.get_by_test_id("session-list")).to_contain_text("Passkey")

    # Remembered browser: the password alone is enough...
    page.get_by_role("button", name="Sign out").first.click()
    page.get_by_label("Email").fill("parent@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_test_id("session-list")).to_contain_text("Password, remembered device")

    # ...but deleting a PC asks to confirm it's you; the passkey answers.
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Devices").click()
    page.get_by_role("button", name="Add a PC").click()
    page.get_by_label("Name", exact=True).fill("Spare PC")
    page.get_by_role("button", name="Create").click()
    page.get_by_role("link", name="Set the schedule").click()
    page.get_by_role("button", name="Delete device").click()
    expect(page.get_by_role("heading", name="Confirm it's you")).to_be_visible()
    shot(page, "10-confirm")
    page.get_by_role("button", name="Use a passkey").click()
    expect(page.get_by_role("heading", name="Devices", exact=True)).to_be_visible()
    expect(page.get_by_text("Spare PC")).to_have_count(0)

    # Forget this browser: the next password sign-in asks for the code again.
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Account").click()
    page.get_by_test_id("remembered-list").get_by_role("button", name="Forget").click()
    expect(page.get_by_text("No remembered devices.")).to_be_visible()
    page.get_by_role("button", name="Sign out").first.click()
    page.get_by_label("Email").fill("parent@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_label("Authentication code")).to_be_visible()

