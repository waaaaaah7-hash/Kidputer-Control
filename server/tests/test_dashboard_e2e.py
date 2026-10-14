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


def shot(page, name: str, locator=None) -> None:
    """Save a screenshot of the page, or of just one element."""
    out = os.environ.get("KC_E2E_SCREENSHOTS")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        path = str(Path(out) / f"{name}.png")
        # Leave out the "Saved" pop-ups: they cover the page.
        page.evaluate("document.querySelector('.toasts')?.style.setProperty('visibility', 'hidden')")
        if locator is None:
            page.screenshot(path=path, full_page=True)
        else:
            locator.screenshot(path=path)
        page.evaluate("document.querySelector('.toasts')?.style.removeProperty('visibility')")


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
    expect(page.get_by_role("heading", name="Your PCs")).to_be_visible()
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
    page.get_by_text("More: lock with a reason").click()
    page.get_by_placeholder("Reason, shown on the lock screen").fill("Homework first")
    page.get_by_role("button", name="Lock with this reason").click()
    expect(page.get_by_text("Locked by a parent")).to_be_visible()
    assert agent.get("/api/v1/agent/state").json()["immediate_lock"]["reason"] == "Homework first"

    # Extra time.
    page.get_by_role("button", name="+30 min").click()
    expect(page.get_by_role("button", name="Cancel")).to_be_visible()
    assert len(agent.get("/api/v1/agent/state").json()["overrides"]) == 1

    # Persistent message, on the Overview.
    page.get_by_role("tab", name="Overview").click()
    expect(page.get_by_role("img", name=re.compile("Locked by the schedule|No scheduled lock today"))).to_be_visible()
    page.get_by_label("Send a message").fill("Dinner in 10 minutes")
    page.get_by_label("Stays until I remove it").check()
    page.get_by_role("button", name="Send", exact=True).click()
    expect(page.locator(".message-body")).to_have_text("Dinner in 10 minutes")
    assert agent.get("/api/v1/agent/state").json()["message"]["mode"] == "persistent"
    shot(page, "04-overview")

    # Schedule: Monday 21:30-07:00, copied to Tue-Fri.
    page.get_by_role("tab", name="Schedule and settings").click()
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
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Home").click()
    expect(page.locator(".device-card .badge")).to_have_text("Tamper alert")
    # Bedtime is 21:30 on weekdays (UTC), so the card counts down to it, or (on a weekend) says there's none soon.
    expect(page.locator(".device-card .countdown")).to_contain_text(re.compile("until bedtime at 21:30|No bedtime lock in the next 24 hours"))
    expect(page.locator(".device-card .pc-card-foot")).to_contain_text("on the PC today")
    shot(page, "05-devices")
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Activity").click()
    expect(page.get_by_text("Immediate lock requested")).to_be_visible()
    page.get_by_label("Importance").select_option("critical")
    expect(page.locator(".event")).to_have_count(1)
    expect(page.get_by_text("Tamper detected")).to_be_visible()
    shot(page, "06-activity")

    # Phone-sized layout.
    page.set_viewport_size({"width": 390, "height": 844})
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Home").click()
    expect(page.locator(".device-card .countdown")).to_be_visible()
    assert_fits_screen(page)
    shot(page, "07-phone-home")
    page.locator(".device-card h2 a").click()
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
    expect(page.get_by_role("heading", name="Your PCs", exact=True)).to_be_visible()

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
    expect(page).to_have_url(re.compile(r"/login$"))
    page.get_by_label("Email").fill("parent@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_test_id("session-list")).to_contain_text("Password, remembered device")

    # ...but deleting a PC asks to confirm it's you; the passkey answers.
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Home").click()
    page.get_by_role("button", name="Add a PC").click()
    page.get_by_label("Name", exact=True).fill("Spare PC")
    page.get_by_role("button", name="Create").click()
    page.get_by_role("link", name="Set the schedule").click()
    page.get_by_role("button", name="Delete device").click()
    expect(page.get_by_role("heading", name="Confirm it's you")).to_be_visible()
    shot(page, "10-confirm")
    page.get_by_role("button", name="Use a passkey").click()
    expect(page.get_by_role("heading", name="Your PCs", exact=True)).to_be_visible()
    expect(page.get_by_text("Spare PC")).to_have_count(0)

    # Forget this browser: the next password sign-in asks for the code again.
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Account").click()
    page.get_by_test_id("remembered-list").get_by_role("button", name="Forget").click()
    expect(page.get_by_text("No remembered devices.")).to_be_visible()
    page.get_by_role("button", name="Sign out").first.click()
    expect(page).to_have_url(re.compile(r"/login$"))
    page.get_by_label("Email").fill("parent@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    expect(page.get_by_label("Authentication code")).to_be_visible()



def test_second_parent_invite_and_removal(page, live_app):
    _, secret = make_admin()
    page.on("dialog", lambda d: d.accept())

    page.goto("/login")
    page.get_by_label("Email").fill("parent@example.com")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    page.get_by_label("Authentication code").fill(pyotp.TOTP(secret).now())
    page.get_by_role("button", name="Verify").click()
    expect(page.get_by_role("heading", name="Your PCs")).to_be_visible()

    page.get_by_role("navigation", name="Main").get_by_role("link", name="Account").click()
    page.get_by_label("Your name").fill("Alex")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator(".user-box")).to_contain_text("Alex")
    expect(page.get_by_test_id("parent-list")).to_contain_text("Alex")

    page.get_by_label("Email of the parent to invite").fill("partner@example.com")
    page.get_by_role("button", name="Invite a parent").click()
    link = page.get_by_test_id("invite-link").locator("code").inner_text()
    assert link.startswith(f"{live_app}/invite#")
    expect(page.get_by_test_id("invite-list")).to_contain_text("partner@example.com")
    shot(page, "20-parents-invite")

    # The partner opens the link in their own browser.
    partner_ctx = page.context.browser.new_context(base_url=live_app, viewport={"width": 390, "height": 844})
    partner = partner_ctx.new_page()
    partner.goto(link)
    expect(partner.get_by_text("Alex invited")).to_be_visible()
    assert_fits_screen(partner)
    shot(partner, "21-invite-accept-phone")
    partner.get_by_label("Your name").fill("Sam")
    partner.get_by_label("Choose a password").fill("another long passphrase")
    partner.get_by_label("Repeat password").fill("another long passphrase")
    partner.get_by_role("button", name="Create my account").click()
    expect(partner).to_have_url(re.compile(r"/setup-2fa$"))
    partner_secret = partner.locator("code.secret").inner_text().replace(" ", "")
    partner.get_by_label("Code from the app").fill(pyotp.TOTP(partner_secret).now())
    partner.get_by_role("button", name="Turn on two-factor sign-in").click()
    expect(partner.get_by_role("heading", name="Your PCs")).to_be_visible()

    # The link only works once.
    used = partner_ctx.new_page()
    used.goto(link)
    expect(used.get_by_text("expired or was already used")).to_be_visible()
    used.close()

    # Both parents are listed, and the activity log says who did what.
    page.reload()
    expect(page.get_by_test_id("parent-list")).to_contain_text("Sam")
    expect(page.get_by_test_id("parent-list")).to_contain_text("partner@example.com")
    expect(page.get_by_test_id("invite-list")).to_have_count(0)
    shot(page, "parents", page.locator(".card", has=page.get_by_test_id("parent-list")))
    page.set_viewport_size({"width": 390, "height": 844})
    assert_fits_screen(page)
    shot(page, "22-parents-phone")
    page.set_viewport_size({"width": 1280, "height": 900})
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Activity").click()
    expect(page.locator(".event", has_text="Parent joined")).to_contain_text("Sam")
    expect(page.locator(".event", has_text="Parent invited")).to_contain_text("Alex")

    # Removing Sam signs them out.
    page.get_by_role("navigation", name="Main").get_by_role("link", name="Account").click()
    sam_row = page.get_by_test_id("parent-list").locator(".list-row", has_text="Sam")
    sam_row.get_by_role("button", name="Remove").click()
    expect(page.get_by_test_id("parent-list")).not_to_contain_text("Sam")
    partner.reload()
    expect(partner).to_have_url(re.compile(r"/login$"))
    partner_ctx.close()


def sign_in_ui(page, secret: str, email: str = "parent@example.com") -> None:
    page.goto("/login")
    page.get_by_label("Email").fill(email)
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Continue").click()
    page.get_by_label("Authentication code").fill(pyotp.TOTP(secret).now())
    page.get_by_role("button", name="Verify").click()
    expect(page.get_by_role("heading", name="Your PCs")).to_be_visible()


def test_notification_settings(page, live_app):
    _, secret = make_admin()
    sign_in_ui(page, secret)
    # The dashboard installs as a phone app: manifest and service worker are in place.
    assert page.evaluate("document.querySelector('link[rel=manifest]').href").endswith("/manifest.webmanifest")
    page.wait_for_function("navigator.serviceWorker.getRegistration().then(r => !!r)")

    page.get_by_role("navigation", name="Main").get_by_role("link", name="Account").click()
    page.get_by_role("link", name="Notification settings").click()
    expect(page.get_by_role("heading", name="Notifications")).to_be_visible()
    expect(page.get_by_text("Email isn't set up on the server yet")).to_be_visible()
    page.get_by_label("Wrong lock-screen PINs").select_option("immediate")
    page.get_by_label("Quiet hours").check()
    page.get_by_role("button", name="Save notification settings").click()
    expect(page.get_by_text("Notification settings saved")).to_be_visible()
    shot(page, "30-notifications")
    page.set_viewport_size({"width": 390, "height": 844})
    assert_fits_screen(page)
    shot(page, "31-notifications-phone")

    page.reload()
    expect(page.get_by_label("Wrong lock-screen PINs")).to_have_value("immediate")
    expect(page.get_by_label("From")).to_have_value("22:00")


def make_enrolled_device(base_url: str, name: str = "Sam's PC") -> httpx.Client:
    """A PC that's already installed: returns an agent HTTP client (with .device_id) for it."""
    from app.db import SessionLocal
    from app.deps import utcnow
    from app.models import Device, DeviceSettings
    from app.security import new_token, token_hash

    token = new_token()
    with SessionLocal() as db:
        d = Device(name=name, timezone="Europe/London", token_hash=token_hash(token), enrolled_at=utcnow(), hostname="SAM-PC", agent_version="1.1.0", last_seen_at=utcnow())
        d.settings = DeviceSettings()
        db.add(d)
        db.commit()
        device_id = str(d.id)
    agent = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"})
    agent.device_id = device_id
    return agent


def test_running_apps_and_closing_one(page, live_app):
    _, secret = make_admin()
    agent = make_enrolled_device(live_app)
    page.on("dialog", lambda d: d.accept())
    sign_in_ui(page, secret)
    page.locator(".device-card h2 a").click()
    page.get_by_role("tab", name="Apps").click()
    expect(page.get_by_text("Waiting for the PC")).to_be_visible()
    # Opening the tab puts the PC on fast polling.
    deadline = time.time() + 5
    while agent.get("/api/v1/agent/state").json()["live_until"] is None:
        assert time.time() < deadline
        time.sleep(0.2)

    mc = "C:\\Program Files\\Minecraft\\runtime\\javaw.exe"
    procs = [
        {"pid": 10, "name": "javaw", "path": mc, "has_window": True, "memory_mb": 1800, "description": "Minecraft", "company": "Mojang", "system": False, "started_at": None, "title": None, "product": None},
        {"pid": 11, "name": "OneDrive", "path": "C:\\Users\\Sam\\OneDrive.exe", "has_window": False, "memory_mb": 40, "description": "Microsoft OneDrive", "company": "Microsoft", "system": False, "started_at": None, "title": None, "product": None},
        {"pid": 12, "name": "explorer", "path": "C:\\Windows\\explorer.exe", "has_window": True, "memory_mb": 90, "description": "Windows Explorer", "company": "Microsoft", "system": True, "started_at": None, "title": None, "product": None},
    ]
    r = agent.post("/api/v1/agent/reports/processes", json={"child_signed_in": True, "processes": procs})
    assert r.status_code == 204, r.text
    apps = page.get_by_test_id("running-apps")
    expect(apps).to_contain_text("Minecraft", timeout=12000)  # the page refreshes every 5 s
    expect(apps).to_contain_text("1.8 GB")
    expect(apps).not_to_contain_text("Windows Explorer")  # Windows' own processes are hidden
    expect(page.get_by_test_id("background-apps")).to_contain_text("Microsoft OneDrive")
    shot(page, "40-apps")

    apps.get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Warn, then close in 1 minute").click()
    expect(page.get_by_test_id("app-stops")).to_contain_text("Closing")
    cmd = agent.get("/api/v1/agent/state").json()["commands"][0]
    assert cmd["kind"] == "stop_app" and cmd["payload"]["path"] == mc and cmd["payload"]["grace_seconds"] == 60
    agent.post(f"/api/v1/agent/commands/{cmd['id']}/result", json={"status": "done", "result": {"closed": 1}})
    expect(page.get_by_test_id("app-stops")).to_contain_text("Closed")

    page.set_viewport_size({"width": 390, "height": 844})
    assert_fits_screen(page)
    shot(page, "41-apps-phone")


def test_app_rules_custom_apps_and_usage(page, live_app):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    _, secret = make_admin()
    agent = make_enrolled_device(live_app)
    agent.post("/api/v1/agent/reports/processes", json={"processes": [
        {"pid": 5, "name": "Terraria", "path": "D:\\Games\\Terraria\\Terraria.exe", "has_window": True, "system": False, "product": "Terraria", "company": "Re-Logic"},
    ]})
    today = datetime.now(ZoneInfo("Europe/London")).date().isoformat()
    agent.post("/api/v1/agent/reports/usage", json={"days": {today: {
        "_total": {"name": "Screen time", "seconds": 5400},
        "roblox": {"name": "Roblox", "category": "games", "seconds": 3000},
        "chrome": {"name": "Google Chrome", "category": "browsers", "seconds": 1800},
    }}})
    page.on("dialog", lambda d: d.accept())
    sign_in_ui(page, secret)
    page.locator(".device-card h2 a").click()

    # A schedule for all games: weekdays 16-19, weekends 10-20 (the defaults).
    page.get_by_role("tab", name="App rules").click()
    expect(page.get_by_text("No rules yet")).to_be_visible()
    page.get_by_role("button", name="Add a rule").click()
    page.get_by_role("button", name="Save rule").click()
    rules = page.get_by_test_id("app-rules")
    expect(rules).to_contain_text("Only Mon–Fri 16:00–19:00, Sat–Sun 10:00–20:00")

    # Block a game the child has been playing that isn't in the catalogue.
    page.get_by_role("button", name="Add a rule").click()
    page.get_by_role("button", name="An app that isn't listed…").click()
    page.get_by_test_id("seen-apps").get_by_role("button", name="Use").click()
    expect(page.get_by_label("Program file name")).to_have_value("Terraria.exe")
    page.get_by_role("button", name="Add app").click()
    expect(page.get_by_label("Which apps")).to_have_value(re.compile(r"^app:custom:"))
    page.get_by_label("Blocked").check()
    page.get_by_role("button", name="Save rule").click()
    expect(rules).to_contain_text("Terraria")
    expect(rules).to_contain_text("Blocked")
    expect(page.get_by_test_id("custom-apps")).to_contain_text("Terraria.exe")
    shot(page, "50-app-rules")

    state = agent.get("/api/v1/agent/state").json()
    assert [r["mode"] for r in state["app_rules"]] == ["block", "schedule"]  # the app's own rule first
    assert any(a["name"] == "Terraria" and a["matchers"] == [{"exe": "Terraria.exe", "product": "Terraria"}] for a in state["app_catalog"])

    page.get_by_role("tab", name="Usage").click()
    expect(page.get_by_role("heading", name="Today")).to_be_visible()
    expect(page.get_by_test_id("usage-apps")).to_contain_text("Roblox")
    expect(page.get_by_test_id("usage-apps")).to_contain_text("50 min")
    expect(page.get_by_test_id("usage-categories")).to_contain_text("Games")
    shot(page, "51-usage")
    page.set_viewport_size({"width": 390, "height": 844})
    assert_fits_screen(page)
    page.get_by_role("tab", name="App rules").click()
    expect(page.get_by_test_id("app-rules")).to_be_visible()
    assert_fits_screen(page)
    shot(page, "52-app-rules-phone")


def test_files_tab(page, live_app):
    _, secret = make_admin()
    agent = make_enrolled_device(live_app)
    GB = 1024 ** 3
    agent.post("/api/v1/agent/reports/drives", json={"drives": [
        {"name": "C:\\", "label": "", "type": "Fixed", "format": "NTFS", "size": 500 * GB, "free": 40 * GB},
        {"name": "E:\\", "label": "SANDISK", "type": "Removable", "format": "FAT32", "size": 32 * GB, "free": 30 * GB},
    ], "devices": ["Sam's iPhone"]})

    def scan(downloads_gb: int) -> dict:
        downloads = {"n": "Downloads", "s": downloads_gb * GB, "a": downloads_gb * GB, "f": 40, "d": 2, "m": 0}
        sam = {"n": "Sam", "s": downloads_gb * GB, "a": downloads_gb * GB, "f": 40, "d": 3, "m": 0, "c": [downloads]}
        users = {"n": "Users", "s": downloads_gb * GB, "a": downloads_gb * GB, "f": 40, "d": 4, "m": 0, "c": [sam]}
        games = {"n": "Games", "s": 200 * GB, "a": 200 * GB, "f": 900, "d": 50, "m": 0}
        total = 200 * GB + downloads_gb * GB
        return {"root": "C:\\", "tree": {"n": "C:\\", "s": total, "a": total, "f": 940, "d": 60, "m": 0, "c": [games, users]},
                "largest": [{"p": "C:\\Games\\Big\\data.pak", "s": 40 * GB, "m": 0}],
                "by_type": {"game data": {"s": 150 * GB, "f": 300}, "video": {"s": 20 * GB, "f": 12}}, "inaccessible": 0}

    agent.post("/api/v1/agent/reports/scan:C", json=scan(10))
    agent.post("/api/v1/agent/reports/scan:C", json=scan(22))
    sign_in_ui(page, secret)
    page.locator(".device-card h2 a").click()
    page.get_by_role("tab", name="Files").click()

    drives = page.get_by_test_id("drives")
    expect(drives).to_contain_text("40 GB free of 500 GB")
    expect(drives).to_contain_text("SANDISK")
    expect(page.get_by_text("Sam's iPhone")).to_be_visible()
    folders = page.get_by_test_id("folders")
    expect(folders).to_contain_text("Games")
    expect(folders).to_contain_text("200 GB")
    expect(page.get_by_test_id("changes")).to_contain_text("C:\\Users\\Sam\\Downloads")
    expect(page.get_by_test_id("changes")).to_contain_text("+12 GB")
    expect(page.get_by_test_id("largest")).to_contain_text("data.pak")
    expect(page.get_by_test_id("by-type")).to_contain_text("Game data")
    shot(page, "60-files")

    # Drill down, then ask the PC what's in the folder right now.
    folders.get_by_role("button", name=re.compile("^Users")).click()
    folders.get_by_role("button", name=re.compile("^Sam")).click()
    expect(page.get_by_role("navigation", name="Folder")).to_contain_text("Users \\ Sam")
    page.get_by_role("button", name="Show what's in this folder now").click()
    cmd = None
    deadline = time.time() + 5
    while cmd is None:
        assert time.time() < deadline
        cmds = [c for c in agent.get("/api/v1/agent/state").json()["commands"] if c["kind"] == "list_folder"]
        cmd = cmds[0] if cmds else None
        time.sleep(0.2)
    assert cmd["payload"]["path"] == "C:\\Users\\Sam"
    agent.post(f"/api/v1/agent/commands/{cmd['id']}/result", json={"status": "done", "result": {
        "path": "C:\\Users\\Sam", "total": 2, "truncated": False,
        "entries": [{"n": "Downloads", "dir": True, "m": "2026-01-12T20:00:00Z", "h": False, "l": False},
                    {"n": "notes.txt", "dir": False, "s": 2048, "m": "2026-01-12T20:00:00Z", "h": False, "l": False}]}})
    listing = page.get_by_test_id("folder-listing")
    expect(listing).to_contain_text("notes.txt")
    expect(listing).to_contain_text("2 KB")

    page.set_viewport_size({"width": 390, "height": 844})
    assert_fits_screen(page)
    shot(page, "61-files-phone")


def test_security_tab(page, live_app):
    from datetime import datetime, timedelta, timezone

    def iso(dt):
        return dt.isoformat().replace("+00:00", "Z")

    now = datetime.now(timezone.utc)
    _, secret = make_admin()
    agent = make_enrolled_device(live_app)
    page.on("dialog", lambda d: d.accept())
    report = {
        "defender": {
            "available": True, "antivirus_enabled": True, "realtime": True, "tamper_protected": True,
            "signatures_updated": iso(now - timedelta(hours=4)), "signature_version": "1.443.12.0",
            "quick_scan_at": iso(now - timedelta(days=1)), "full_scan_at": iso(now - timedelta(days=5)),
            "threats": [
                {"id": "{B}", "name": "Trojan:Win32/Wacatac.B!ml", "severity": "severe", "status": "remove_failed", "needs_action": True,
                 "detected_at": iso(now - timedelta(hours=2)), "files": ["C:\\Users\\Sam\\Downloads\\mods.zip"]},
                {"id": "{A}", "name": "PUA:Win32/Presenoker", "severity": "low", "status": "quarantined", "needs_action": False,
                 "detected_at": iso(now - timedelta(days=3)), "files": ["C:\\Users\\Sam\\Downloads\\setup.exe"]},
            ],
            "other_antivirus": [],
        },
        "updates": {
            "windows": {"name": "Windows 11 Home", "version": "24H2", "build": "26100.4652", "edition": "Core"},
            "pause_blocking": "unsupported", "install_waiting": "unmetered",
            "reboot_required": True, "reboot_required_since": iso(now - timedelta(hours=6)), "paused_until": None, "service_disabled": False,
            "last_search_at": iso(now - timedelta(hours=1)), "last_installed_at": iso(now - timedelta(days=2)),
            "pending": [
                {"title": "2026-10 Cumulative Update for Windows 11 Version 24H2 for x64-based Systems (KB5066835)", "kb": "KB5066835",
                 "kind": "security", "size": 812 * 1024 * 1024, "severity": "Critical", "downloaded": True},
                {"title": "Windows 11, version 25H2", "kb": None, "kind": "feature", "size": 3 * 1024**3, "severity": "", "downloaded": False},
            ],
            "last_check_at": iso(now - timedelta(hours=1)), "last_install": None,
        },
        "running": None,
        "queued": [],
    }
    assert agent.post("/api/v1/agent/reports/security", json=report).status_code == 204
    sign_in_ui(page, secret)

    # The home card says what needs attention, and leads to the Security tab.
    line = page.locator(".pc-card-security")
    expect(line).to_contain_text("Threat needs action: Trojan:Win32/Wacatac.B!ml")
    line.click()
    expect(page.get_by_role("tab", name="Security")).to_have_attribute("aria-selected", "true")
    expect(page.get_by_test_id("virus-status")).to_contain_text("Real-time protection")
    threats = page.get_by_test_id("threats")
    expect(threats).to_contain_text("Couldn't remove")
    expect(threats).to_contain_text("Quarantined")
    expect(page.get_by_test_id("update-status")).to_contain_text("Restart needed to finish installing")
    expect(page.get_by_test_id("pending-updates")).to_contain_text("KB5066835")
    expect(page.get_by_test_id("pending-updates")).to_contain_text("New Windows version")
    expect(page.get_by_text("Can't be blocked on Windows Home")).to_be_visible()
    expect(page.get_by_test_id("install-waiting")).to_contain_text("metered connection")
    shot(page, "70-security")

    # Quick scan: the PC gets the request; while it runs, the tab says so.
    page.get_by_role("button", name="Quick scan").click()
    expect(page.get_by_text("Quick scan requested")).to_be_visible()
    cmds = agent.get("/api/v1/agent/state").json()["commands"]
    assert [(c["kind"], c["payload"]) for c in cmds] == [("security", {"action": "quick_scan"})]
    agent.post("/api/v1/agent/reports/security", json=dict(report, running={"action": "quick_scan", "started_at": iso(now)}))
    expect(page.get_by_test_id("security-activity")).to_contain_text("Quick virus scan", timeout=10000)
    page.get_by_role("button", name="Remove threats").click()
    expect(page.get_by_text("Asked the PC to remove the threats")).to_be_visible()
    assert ("security", {"action": "remove_threats"}) in [(c["kind"], c["payload"]) for c in agent.get("/api/v1/agent/state").json()["commands"]]

    # Settings: only report updates, no weekly full scan.
    page.get_by_label("Only show me what's waiting").check()
    page.get_by_label("Full virus scan every week, at bedtime").uncheck()
    page.get_by_label("Stop my child pausing Windows updates").uncheck()
    page.get_by_role("button", name="Save security settings").click()
    expect(page.get_by_text("Security settings saved")).to_be_visible()
    sec = agent.get("/api/v1/agent/state").json()["security"]
    assert sec["updates_mode"] == "report" and sec["weekly_full_scan"] is False and sec["block_pause_updates"] is False

    page.set_viewport_size({"width": 390, "height": 844})
    assert_fits_screen(page)
    shot(page, "71-security-phone")
