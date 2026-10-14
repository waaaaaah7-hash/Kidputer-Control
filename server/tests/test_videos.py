"""Record the instructional videos: real screen recordings of the dashboard with narration and captions.

Skipped unless KC_VIDEOS_DIR is set. Needs a built dashboard (web/dist), Playwright, and for the
narration `pip install kokoro-onnx soundfile imageio-ffmpeg` plus the Kokoro model files
(kokoro.onnx, voices.bin) in KC_VIDEO_MODEL_DIR. See docs/VIDEOS.md.

    KC_VIDEOS_DIR=../videos pytest tests/test_videos.py            # all videos
    KC_VIDEOS_DIR=../videos pytest tests/test_videos.py -k tour    # one
"""

import os
import re
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyotp
import pytest

from .conftest import PASSWORD, make_admin
from .test_dashboard_e2e import DIST, expect, live_app  # noqa: F401  (live_app is a fixture)

pytestmark = [
    pytest.mark.skipif(not os.environ.get("KC_VIDEOS_DIR"), reason="set KC_VIDEOS_DIR to record the videos"),
    pytest.mark.skipif(not (DIST / "index.html").is_file(), reason="dashboard not built"),
]
OUT = Path(os.environ.get("KC_VIDEOS_DIR", "videos")).resolve()


@pytest.fixture
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("KC_CHROMIUM_PATH") or None)
        yield b
        b.close()


@pytest.fixture
def narrator():
    from .videokit import Narrator

    return Narrator(OUT / ".narration-cache")


def recorder(browser, narrator, name, base_url=None, phone=False):
    from .videokit import Recorder

    return Recorder(browser, name, OUT, narrator, base_url=base_url, phone=phone)


# --- Sample family ------------------------------------------------------------------------------------

def family(base_url: str, devices: bool = True, threat: bool = False, updates: bool = False) -> dict:
    """Alex (a parent) and, optionally, Sam's PC (played by FakePC) and an offline family laptop."""
    from app.db import SessionLocal
    from app.deps import utcnow
    from app.models import AdminUser, Device, DeviceSettings, ScheduleEntry
    from app.security import new_token, token_hash

    from .videokit import APPS, FakePC, drives_report, pick_timezone, scan_report, security_report, usage_report

    user, secret = make_admin()
    with SessionLocal() as db:
        db.get(AdminUser, user.id).display_name = "Alex"
        db.commit()
    world = {"secret": secret, "tz": pick_timezone()}
    if not devices:
        return world
    tz = world["tz"]
    tokens = {}
    with SessionLocal() as db:
        for name, host in (("Sam's PC", "SAM-PC"), ("Family laptop", "FAMILY-LAPTOP")):
            token = new_token()
            d = Device(name=name, timezone=tz, token_hash=token_hash(token), enrolled_at=utcnow() - timedelta(days=12), hostname=host,
                       agent_version="1.3.0", last_seen_at=utcnow())
            d.settings = DeviceSettings()
            d.schedule = [ScheduleEntry(day_of_week=i, lock_at=dtime(21, 0), unlock_at=dtime(7, 0)) for i in range(5)] + [
                ScheduleEntry(day_of_week=i, lock_at=dtime(22, 30), unlock_at=dtime(8, 0)) for i in (5, 6)]
            db.add(d)
            db.flush()
            tokens[name] = token
            world[name] = str(d.id)
        db.commit()

    sam = FakePC(base_url, tokens["Sam's PC"])
    sam.processes = [dict(p) for p in APPS]
    from .videokit import SAM_LISTING

    sam.listings = SAM_LISTING
    today = datetime.now(ZoneInfo(tz)).date().isoformat()
    sam.report("usage", usage_report(today))
    sam.report("drives", drives_report())
    sam.report("scan:C", scan_report(8))
    sam.report("scan:C", scan_report(20))
    sam.security = security_report(threat=threat, updates=updates)
    sam.report("security", sam.security)
    sam.http.post("/api/v1/agent/heartbeat", json={"agent_version": "1.3.0", "status": {"mode": "none", "reason": "none", "ui_agent_alive": True, "console_user_is_child": True}})
    sam.event("pc.resumed", "The PC woke from sleep", ago=timedelta(minutes=96))
    sam.event("software.installed", "Steam installed", severity="warning", ago=timedelta(minutes=88))
    sam.event("program.first_run", "OptiFine installer ran for the first time, from Downloads\\modpack.zip", severity="warning", ago=timedelta(minutes=70))
    sam.event("usb.connected", "USB drive SANDISK (E:) connected: 3 programs, 1 archive", severity="warning", ago=timedelta(minutes=52))
    sam.event("app.blocked", "Roblox was closed: games start at 16:00 on school days", severity="warning", ago=timedelta(minutes=41))
    world["sam"] = sam.start()

    laptop = FakePC(base_url, tokens["Family laptop"])
    laptop.report("usage", {"days": {today: {"_total": {"name": "Screen time", "seconds": 2880}}}})
    laptop.http.post("/api/v1/agent/heartbeat", json={"agent_version": "1.3.0", "status": {"mode": "none", "reason": "none", "ui_agent_alive": True, "console_user_is_child": False}})
    laptop.event("pc.shutting_down", "The PC is shutting down", ago=timedelta(minutes=100))
    with SessionLocal() as db:
        db.get(Device, world["Family laptop"]).last_seen_at = utcnow() - timedelta(minutes=100)
        db.commit()
    return world


def sign_in(r, secret: str, visible: bool = True) -> None:
    page = r.page
    page.goto("/login")
    if visible:
        r.type(page.get_by_label("Email"), "parent@example.com")
        r.type(page.get_by_label("Password"), PASSWORD, delay=25)
        r.click(page.get_by_role("button", name="Continue"))
        r.type(page.get_by_label("Authentication code"), pyotp.TOTP(secret).now(), delay=90)
        r.click(page.get_by_role("button", name="Verify"))
    else:
        r.card("Kidputer Control", "")
        page.get_by_label("Email").fill("parent@example.com")
        page.get_by_label("Password").fill(PASSWORD)
        page.get_by_role("button", name="Continue").click()
        page.get_by_label("Authentication code").fill(pyotp.TOTP(secret).now())
        page.get_by_role("button", name="Verify").click()
    expect(page.get_by_role("heading", name="Your PCs")).to_be_visible()


def main_nav(page, name: str):
    return page.get_by_role("navigation", name="Main").get_by_role("link", name=name)


# --- 1. Tour --------------------------------------------------------------------------------------------

def test_01_tour(browser, narrator, live_app):
    w = family(live_app)
    r = recorder(browser, narrator, "01-tour", live_app)
    p = r.page
    p.goto("/login")
    r.card("Kidputer Control", "A quick tour of the parent dashboard")
    r.narrate(
        "Welcome to Kidputer Control. It sets bedtimes for your child's Windows PC, and lets you control it from any browser or phone.",
        "Here's a quick tour of the dashboard.",
    )
    r.uncard()
    with r.say("You sign in with your email and password, then a six-digit code from your authenticator app."):
        sign_in(r, w["secret"])
    r.narrate("You can also add a passkey, so a fingerprint or face scan signs you in.")

    card = p.locator(".device-card", has_text="Sam's PC")
    expect(card.locator(".countdown")).to_be_visible()
    with r.say("This is the home page. Each PC has a card. The big number is how long your child has left before the bedtime lock."):
        r.point(card.locator(".countdown-value"))
    with r.say("The three big buttons do what parents need most: lock the PC right now,"):
        r.point(card.get_by_role("button", name="Lock now"))
    with r.say("give thirty minutes of extra time,"):
        r.point(card.get_by_role("button", name="+30 min"))
    with r.say("or put a message on the screen."):
        r.point(card.get_by_role("button", name="Message"))
    with r.say("Under the buttons, one line sums up the PC's security: its virus protection and Windows updates. Here, all is well."):
        r.point(card.locator(".pc-card-security"))
    with r.say("Beside the cards is today's activity: apps that were blocked, programs that were installed, and everything parents did."):
        r.point(p.locator(".home-feed .card-head"))
    with r.say("The sidebar lists your PCs. A green dot means the PC is on and checking in; grey means it's off or asleep."):
        r.point(p.get_by_role("navigation", name="PCs").get_by_role("link", name="Family laptop"))

    with r.say("Let's open Sam's PC."):
        r.click(card.locator("h2 a"))
    with r.say("At the top are its status, the countdown to bedtime, and all the quick actions."):
        r.point(p.locator(".hero-countdown"))
    r.scroll_to(p.locator(".timeline"))
    with r.say("This bar shows the day, hour by hour. Green is when the PC may be used, beige is when the bedtime schedule locks it, and the orange line is now."):
        r.point(p.locator(".timeline"))

    running = p.locator(".card", has=p.get_by_role("heading", name="Open right now"))
    r.scroll_to(running)
    expect(running).to_contain_text("Minecraft", timeout=15000)
    with r.say("Here are the apps open on the PC right now. Click Close, and your child gets a one-minute warning to save their work first."):
        r.click(running.locator(".list-row", has_text="Discord").get_by_role("button", name="Close"))
        r.point(p.get_by_role("button", name="Warn, then close in 1 minute"))
    r.click(p.get_by_role("button", name="Warn, then close in 1 minute"))
    with r.say("When the minute is up, the app closes, and it disappears from the list."):
        expect(running).not_to_contain_text("Discord", timeout=20000)

    box = p.get_by_label("Send a message")
    r.scroll_to(box)
    with r.say("To put a message on the screen, type it here. Choose whether your child can close it, or whether it stays until you remove it."):
        r.type(box, "Dinner in 10 minutes!")
        r.point(p.get_by_label("Child can close it"))
    r.click(p.get_by_role("button", name="Send", exact=True))
    with r.say("The message appears on the PC straight away, and the dashboard shows when it was seen."):
        expect(p.locator(".message-body")).to_be_visible()
        r.point(p.locator(".message-body"))

    r.scroll_top()
    with r.say("Lock now locks the PC immediately, with no countdown."):
        r.click(p.get_by_role("button", name="Lock now"))
        expect(p.get_by_text("The PC has locked.")).to_be_visible(timeout=15000)
        r.point(p.get_by_text("The PC has locked."))
    with r.say("End immediate lock gives it back."):
        r.click(p.get_by_role("button", name="End immediate lock"))
    with r.say("Extra time unlocks the PC for longer, even during the bedtime schedule. Each grant is listed here, and you can cancel it."):
        r.click(p.get_by_role("button", name="+30 min"))
        r.point(p.locator(".hero .list-row"))
    r.click(p.locator(".hero .list-row").get_by_role("button", name="Cancel"))

    with r.say("The tabs cover the rest: apps, app rules, usage time, files, security, and the schedule and settings."):
        r.point(p.get_by_role("tab", name="Apps"))
        r.point(p.get_by_role("tab", name="Security"), settle=0.6)
    r.click(p.get_by_role("tab", name="Schedule and settings"))
    r.scroll_to(p.locator(".schedule"))
    r.narrate("Here's the bedtime schedule: a lock time and an unlock time for each night, in the PC's own time zone.")

    with r.say("The Activity page is one log of everything, from every PC and every parent. You can filter it by PC, by importance, and by who did it."):
        r.click(main_nav(p, "Activity"))
        r.point(p.get_by_label("Importance"))
    with r.say("And under Notifications, you choose what you're told about, and how: by email, or as a notification on your phone."):
        r.click(main_nav(p, "Notifications"))
    r.wait(1.5)
    r.card("That's the tour", "Next: setting up on Render, installing on your child's PC, and using it from your phone.")
    r.narrate("That's the tour. The other videos show how to set everything up, how to use it from your phone, and the security tab.")
    w["sam"].stop()
    r.finish()


# --- 2. Render ----------------------------------------------------------------------------------------

def test_02_render_setup(browser, narrator, live_app):
    r = recorder(browser, narrator, "02-render-setup", live_app)
    p = r.page
    r.title_slide("Setting up the server on Render", "Put Kidputer Control online, once, in about fifteen minutes")
    r.narrate(
        "This video shows how to put Kidputer Control online, using a hosting service called Render.",
        "You only do this once, and it takes about fifteen minutes.",
    )
    r.slide("""<div class='slide'><p class='eyebrow'>The big picture</p><h1>What you're creating</h1>
      <div class='diagram'>
        <div class='box'>Your phone or computer<small>the dashboard</small></div><div class='arrow'>&rarr;</div>
        <div class='box green'>Render web service<small>dashboard + API</small></div><div class='arrow'>&rarr;</div>
        <div class='box'>Render database<small>Postgres</small></div>
      </div>
      <div class='diagram'><div class='box'>Your child's PC<small>Kidputer Control agent</small></div><div class='arrow'>&nearr;</div></div></div>""")
    r.reveal(1)
    with r.say("Everything runs as one web service, plus one database, on Render."):
        r.reveal(2)
    r.reveal(0)
    with r.say("You open the dashboard from your phone or computer."):
        pass
    r.reveal(3)
    r.narrate("And your child's PC checks in with the same service, over a secure connection. If the PC goes offline, it keeps enforcing the last schedule it was given.")

    r.steps("Before you start", "You'll need", [
        ("A <b>Render</b> account, at render.com", "You'll need a Render account. Sign up at render dot com."),
        ("A <b>GitHub</b> account with this repository", "A GitHub account that holds this project's code. Render builds the service from it."),
        ("An <b>authenticator app</b> on your phone", "And an authenticator app on your phone, such as Google Authenticator or Microsoft Authenticator, for the six-digit sign-in codes."),
    ], numbered=False, note="Paid plans are recommended: a free service sleeps when idle, and free databases are deleted after 30 days.")
    r.narrate("Use Render's paid plans. A free service falls asleep when nobody is using it, and a free database is deleted after thirty days. Check Render's pricing page for the current cost.")

    r.steps("Step 1", "Create the services from the Blueprint", [
        ("In Render, choose <span class='k'>New</span> &rarr; <span class='k'>Blueprint</span>", "In Render, click New, then Blueprint."),
        ("Pick this repository", "Pick this repository from your GitHub account."),
        ("Render proposes <code>kidputer-control</code> (web service) and <code>kidputer-control-db</code> (database)",
         "Render reads the settings file in the project, and proposes two things: the kidputer control web service, and its database."),
    ])
    r.steps("Step 2", "Fill in two values, then Apply", [
        ("<code>KC_BOOTSTRAP_ADMIN_EMAIL</code>: your email", "Render asks for two values. The first is your email address."),
        ("<code>KC_BOOTSTRAP_ADMIN_PASSWORD</code>: at least 12 characters", "The second is a password of at least twelve characters. You'll change it later."),
        ("Click <span class='k'>Apply</span> and wait for the first deploy", "Click Apply. The first deploy takes several minutes. Render builds the app, sets up the database, and creates your parent account."),
    ])

    r.steps("Step 3", "Sign in and turn on two-factor sign-in", [
        ("Open the service's address, e.g. <code>kidputer-control.onrender.com</code>", "When it's ready, open the address Render gives the service."),
        ("Sign in with that email and password", "Sign in with the email and password you just entered."),
        ("Scan the QR code with your authenticator app", "You'll be asked to set up two-factor sign-in. Here's what that looks like."),
    ])
    make_admin(totp=False)
    p.goto("/login")
    r.type(p.get_by_label("Email"), "parent@example.com")
    r.type(p.get_by_label("Password"), PASSWORD, delay=25)
    r.click(p.get_by_role("button", name="Continue"))
    expect(p.get_by_role("img", name=re.compile("QR code"))).to_be_visible()
    with r.say("Scan this QR code with your authenticator app. If you can't scan it, type in the key shown under it instead."):
        r.point(p.get_by_role("img", name=re.compile("QR code")))
    secret = p.locator("code.secret").inner_text().replace(" ", "")
    with r.say("Then type the six-digit code the app shows, and turn it on."):
        r.type(p.get_by_label("Code from the app"), pyotp.TOTP(secret).now(), delay=90)
        r.click(p.get_by_role("button", name="Turn on two-factor sign-in"))
    expect(p.get_by_role("heading", name="Your PCs")).to_be_visible()
    r.narrate("You're in. The dashboard is empty until you add your child's PC, which the next video covers.")

    r.steps("Step 4", "Tidy up", [
        ("In Render, delete <code>KC_BOOTSTRAP_ADMIN_PASSWORD</code> from the service's <b>Environment</b>",
         "Now tidy up. In Render, open the service's Environment, and delete the bootstrap password. It's only needed for the very first start."),
        ("In the dashboard, open <b>Parents and account</b> and change your password", "Then, in the dashboard, change your password on the Account page."),
        ("Add a passkey there too, if your phone supports it", "While you're there, add a passkey, so you can sign in with your fingerprint or face."),
    ])
    r.steps("Optional", "Email alerts", [
        ("Push notifications to your phone need no setup", "Notifications to your phone work without any setup."),
        ("For email, add <code>KC_SMTP_HOST</code>, <code>_PORT</code>, <code>_USERNAME</code>, <code>_PASSWORD</code>, <code>_FROM</code> in Render's Environment",
         "For email alerts, add your email service's details to the Environment in Render. Any service that offers SMTP works, including Gmail with an app password."),
        ("Then click <span class='k'>Send a test</span> on the Notifications page", "Save, and Render restarts the service. Then use Send a test, on the Notifications page."),
    ])
    r.steps("Keeping it running", "After setup", [
        ("Updates: merging <b>DEV</b> into <b>Main</b> redeploys automatically", "Updates are automatic. When the DEV branch is merged into Main, Render rebuilds and redeploys the service."),
        ("Backups: paid Render databases are backed up automatically", "Paid Render databases are backed up automatically."),
        ("Lost your authenticator? Render Shell: <code>python -m app.cli reset-totp you@example.com</code>",
         "And if you lose your authenticator, you can reset it from Render's shell with one command, shown here."),
    ], numbered=False)
    r.title_slide("Next: install on your child's PC", "Add the PC in the dashboard and run the installer")
    r.narrate("That's the server done. Next, install Kidputer Control on your child's PC.")
    r.finish()


# --- 3. Install on the child's PC -------------------------------------------------------------------------

def test_03_install_agent(browser, narrator, live_app):
    from .videokit import FakePC

    w = family(live_app, devices=False)
    r = recorder(browser, narrator, "03-install-on-pc", live_app)
    p = r.page
    r.title_slide("Installing on your child's PC", "Add the PC, download the installer, run it, set the schedule")
    r.narrate("This video shows how to add your child's PC, install Kidputer Control on it, and set a bedtime.")
    r.steps("Before you start", "Two kinds of Windows account", [
        ("You install as a parent, with an <b>administrator</b> account", "You install it while signed in to Windows as a parent, with an administrator account."),
        ("<b>Managed</b> (recommended): your child has a <b>standard</b> account and can't switch it off",
         "Your child's account decides the mode. Managed is recommended: your child has a standard account, and can't switch Kidputer Control off."),
        ("<b>Trusted</b>: your child is an administrator; the schedule applies, but they <i>could</i> switch it off, so you're alerted if they do",
         "Trusted is for a child whose account is an administrator. The schedule still applies, but they could switch it off, so you'll be alerted if the PC goes silent."),
    ], numbered=False)

    sign_in(r, w["secret"], visible=False)
    r.uncard()
    with r.say("In the dashboard, click Add a PC."):
        r.click(p.get_by_role("button", name="Add a PC"))
    with r.say("Give it a name, and pick the time zone the schedule should follow."):
        r.type(p.get_by_label("Name", exact=True), "Sam's PC")
        r.point(p.get_by_label("Time zone (the schedule uses this)"))
        p.get_by_label("Time zone (the schedule uses this)").select_option(w["tz"])
    r.click(p.get_by_role("button", name="Create"))
    with r.say("You can type your child's Windows account name now, or leave it blank and pick it during the install."):
        r.type(p.get_by_label("Child's Windows account"), "Sam")
    with r.say("Choose the mode that matches their account. Then click Download installer."):
        r.point(p.locator(".mode-choice"))
        r.click(p.get_by_role("button", name="Download installer"))
    expect(p.locator(".steps-done")).to_be_visible()
    with r.say("You get a small zip file. It already contains your dashboard's address, and a one-time code that's valid for twenty-four hours."):
        r.point(p.locator(".steps-done"))
    with r.say("If you'd rather install by hand from PowerShell, the command is under Install by hand instead."):
        r.click(p.locator("summary", has_text="Install by hand instead"))
        r.point(p.locator("pre.command"))
    code = re.search(r"-EnrollmentCode (\S+)", p.locator("pre.command").inner_text()).group(1)

    r.steps("On your child's PC", "Run the installer", [
        ("Copy the zip to the PC and unzip it", "Now, on your child's PC. Copy the zip across, for example on a USB stick, and unzip it."),
        ("Signed in as the parent, double-click <b>Install Kidputer Control</b>", "Signed in as the parent, double-click Install Kidputer Control."),
        ("Click <span class='k'>Yes</span> when Windows asks for permission", "Windows asks whether to allow it to make changes. Click Yes."),
        ("If you see <i>Windows protected your PC</i>: <span class='k'>More info</span> &rarr; <span class='k'>Run anyway</span>",
         "If a blue box says Windows protected your PC, click More info, then Run anyway."),
        ("Pick your child's account from the numbered list", "A list of the PC's accounts appears. Type the number of your child's account. That's it."),
    ], note="It starts straight away, and again after every restart. Your child can't close it in Managed mode.")

    pc = FakePC.enroll(live_app, code)
    pc.processes = []
    pc.start()
    p.goto("/")
    card = p.locator(".device-card", has_text="Sam's PC")
    expect(card.locator(".badge")).to_have_text("Unlocked", timeout=15000)
    with r.say("Back in the dashboard, the PC shows up within a minute, online and unlocked."):
        r.point(card.locator(".badge"))
    r.click(card.locator("h2 a"))
    with r.say("Now set the bedtime. Open Schedule and settings."):
        r.click(p.get_by_role("tab", name="Schedule and settings"))
    r.scroll_to(p.locator(".schedule"))
    with r.say("Tick a night, and set when the PC locks and when it unlocks the next morning."):
        r.click(p.get_by_role("checkbox", name="Monday"))
        r.click(p.get_by_label("Monday lock time"))
        p.get_by_label("Monday lock time").fill("21:00")
        r.click(p.get_by_label("Monday unlock time"))
        p.get_by_label("Monday unlock time").fill("07:00")
    with r.say("Copy Monday to the other school nights, then save."):
        r.click(p.get_by_role("button", name="Copy Monday to Tue–Fri"))
        r.click(p.get_by_role("button", name="Save schedule"))
    r.narrate("Before it locks, your child sees a five-minute countdown in the corner of the screen.")
    pin = p.get_by_label("New PIN")
    r.scroll_to(pin)
    with r.say("Finally, set a lock-screen PIN. Typing it on the PC unlocks it for an hour, which is handy when you're standing next to it."):
        r.type(pin, "4321", delay=150)
        r.type(p.get_by_label("Repeat PIN"), "4321", delay=150)
        r.click(p.get_by_role("button", name="Set PIN"))
    expect(p.get_by_role("button", name="Change PIN")).to_be_visible()

    r.steps("Check it works", "A quick test", [
        ("Set a lock time two minutes from now", "Now test it. Set tonight's lock time two minutes from now."),
        ("The countdown appears, then Windows locks", "The countdown appears in the bottom-right corner, and then Windows locks."),
        ("Sign back in: the full-screen lock screen asks for the PIN", "Sign back in, and a full-screen lock screen asks for the PIN."),
        ("Put the time back afterwards", "Put the schedule back afterwards. There's a separate video with the full checklist."),
    ])
    r.steps("Later", "Updating and removing", [
        ("Updates install themselves (switch off per PC in Settings)", "Kidputer Control updates itself on the PC. You can turn that off per PC."),
        ("To move to a new PC: <b>Reinstall or move to another PC</b> on the device's page", "To move it to another PC, use Reinstall or move to another PC, on the device's page."),
        ("To remove: run <code>Uninstall.ps1</code> as administrator, then delete the PC in the dashboard", "To remove it, run the uninstall script as an administrator, then delete the PC in the dashboard."),
    ], numbered=False)
    r.title_slide("You're set up", "Next: using Kidputer Control from your phone")
    r.narrate("You're all set up. The next video shows everyday use from your phone.")
    pc.stop()
    r.finish()


# --- 4. Phone --------------------------------------------------------------------------------------------

def test_04_phone(browser, narrator, live_app):
    w = family(live_app)
    r = recorder(browser, narrator, "04-from-your-phone", live_app, phone=True)
    p = r.page
    r.title_slide("Using it from your phone", "Lock, extra time and messages, wherever you are")
    r.narrate("Most of the time, you'll use Kidputer Control from your phone. Here's how.")
    r.steps("First", "Put it on your home screen", [
        ("<b>iPhone:</b> open it in Safari, tap <span class='k'>Share</span> &rarr; <span class='k'>Add to Home Screen</span>",
         "First, put it on your home screen, so it opens like an app. On an iPhone, open the dashboard in Safari, tap Share, then Add to Home Screen."),
        ("<b>Android:</b> in Chrome, tap the menu &rarr; <span class='k'>Install app</span>", "On Android, open it in Chrome, tap the menu, then Install app."),
    ], numbered=False)
    sign_in(r, w["secret"], visible=False)
    card = p.locator(".device-card", has_text="Sam's PC")
    expect(card.locator(".countdown")).to_be_visible()
    r.uncard()
    with r.say("The home screen shows each PC, with how long is left until bedtime."):
        r.point(card.locator(".countdown-value"))
    with r.say("Tap Lock now to lock it straight away."):
        r.click(card.get_by_role("button", name="Lock now"))
        expect(card.get_by_role("button", name="End lock")).to_be_visible(timeout=10000)
    with r.say("The button changes to End lock. Tap it to unlock the PC again."):
        r.click(card.get_by_role("button", name="End lock"))
    with r.say("Plus thirty minutes gives extra time, even after bedtime."):
        r.click(card.get_by_role("button", name="+30 min"))
    with r.say("And Message puts a note on the screen."):
        r.click(card.get_by_role("button", name="Message"))
        r.type(p.get_by_label("Send a message"), "Bedtime in 5 minutes, please save your game")
    r.click(p.get_by_role("button", name="Send", exact=True))
    r.scroll_to(p.locator(".home-feed .card-head"), block="start")
    r.narrate("Below the cards is everything that happened today.")
    r.scroll_top()

    with r.say("Tap a PC's name for more. Its page has the same buttons, and a few more."):
        r.click(card.locator("h2 a"))
    running = p.locator(".card", has=p.get_by_role("heading", name="Open right now"))
    r.scroll_to(running)
    expect(running).to_contain_text("Minecraft", timeout=15000)
    with r.say("You can see which apps are open, and close one. Your child gets a minute's warning to save first."):
        r.click(running.locator(".list-row", has_text="Minecraft").get_by_role("button", name="Close"))
    r.click(p.get_by_role("button", name="Warn, then close in 1 minute"))
    r.wait(1)

    with r.say("The menu bar along the bottom takes you to the activity log,"):
        r.click(main_nav(p, "Activity"))
    with r.say("and to your alerts."):
        r.click(main_nav(p, "Alerts"))
    button = p.get_by_role("button", name="Turn on for this device")
    r.scroll_to(button)
    with r.say("Tap Turn on for this device, and your phone will show notifications, for example when a PC goes silent, or an app gets installed."):
        r.point(button)
    r.narrate("On an iPhone, this only works once the dashboard is on your home screen, and opened from there.")
    r.title_slide("That's it", "Lock, extra time and messages, from anywhere")
    r.narrate("That's everything you need day to day.")
    w["sam"].stop()
    r.finish()


# --- 5. New on DEV (version 1.2) -----------------------------------------------------------------------

def test_05_rules_usage_files(browser, narrator, live_app):
    w = family(live_app)
    r = recorder(browser, narrator, "05-rules-usage-files", live_app)
    p = r.page
    r.title_slide("App rules, usage and files", "When apps may be used, screen time, drives and folders, and change alerts")
    r.narrate(
        "This video covers app rules, usage time, the files tab, and change alerts.",
    )
    sign_in(r, w["secret"], visible=False)
    r.click(p.get_by_role("link", name="Sam's PC").last)
    r.uncard()

    r.click(p.get_by_role("tab", name="Apps"))
    apps = p.get_by_test_id("running-apps")
    expect(apps).to_contain_text("Minecraft", timeout=15000)
    with r.say("The Apps tab lists everything open on the PC, updated every few seconds while you watch."):
        r.point(apps)
    with r.say("Programs without a window, like updaters, are listed separately. Windows' own programs are hidden."):
        r.point(p.get_by_test_id("background-apps"))
    with r.say("Window titles, like browser tab names, are hidden by default, for your child's privacy. You can switch them on here."):
        r.point(p.get_by_role("button", name="Show window titles"))

    r.click(p.get_by_role("tab", name="App rules"))
    with r.say("App rules decide when apps may be used. Click Add a rule."):
        r.click(p.get_by_role("button", name="Add a rule"))
    with r.say("Pick an app, or a whole category, like games, browsers, chat or video. Then choose allowed, blocked, or allowed only at set times."):
        r.point(p.get_by_label("Which apps"))
    with r.say("The suggested times for games are four to seven on school days, and ten to eight at weekends. Change them to suit you, and save."):
        r.click(p.get_by_role("button", name="Save rule"))
    rules = p.get_by_test_id("app-rules")
    expect(rules).to_contain_text("Games")
    with r.say("Outside those times, a game closes as soon as it opens, and your child sees when it's allowed. A game that's already open gets a countdown."):
        r.point(rules)
    with r.say("For an app that isn't in the list, add it yourself. You can pick from the apps your child has actually used."):
        r.click(p.get_by_role("button", name="Add a rule"))
        r.click(p.get_by_role("button", name="An app that isn't listed…"))
        r.click(p.get_by_test_id("seen-apps").locator(".list-row", has_text="Terraria").get_by_role("button", name="Use"))
    r.click(p.get_by_role("button", name="Add app"))
    with r.say("Here, Terraria gets blocked completely. Renaming the program file doesn't get around it."):
        r.click(p.get_by_label("Blocked"))
        r.click(p.get_by_role("button", name="Save rule"))
    expect(rules).to_contain_text("Terraria")

    r.click(p.get_by_role("tab", name="Usage"))
    with r.say("The Usage tab shows screen time per day: by category, and by app. Only the app in front counts, and idle time doesn't."):
        r.point(p.get_by_test_id("usage-apps"))
    with r.say("On the right are the last seven days."):
        r.point(p.get_by_test_id("usage-days"))

    r.click(p.get_by_role("tab", name="Files"))
    drives = p.get_by_test_id("drives")
    expect(drives).to_contain_text("SANDISK")
    with r.say("The Files tab shows the PC's drives and how full they are, including USB sticks, and phones plugged in by cable."):
        r.point(drives)
    folders = p.get_by_test_id("folders")
    r.scroll_to(folders)
    with r.say("Folder sizes show what's taking up the space. The PC scans once a week, slowly, in the background, so games aren't slowed down."):
        r.point(folders)
    with r.say("Click a folder to look inside."):
        r.click(folders.get_by_role("button", name=re.compile("^Users")))
        r.click(folders.get_by_role("button", name=re.compile("^Sam")))
    with r.say("And Show what's in this folder now asks the PC for the folder's contents right now."):
        r.click(p.get_by_role("button", name="Show what's in this folder now"))
    listing = p.get_by_test_id("folder-listing")
    expect(listing).to_contain_text("Downloads", timeout=15000)
    r.narrate("You see names, sizes and dates only. Files can't be opened or downloaded from the dashboard. That's deliberate, for your child's privacy.")
    changes = p.get_by_test_id("changes")
    r.scroll_to(changes)
    with r.say("What changed lists the folders that grew since the last scan. Here, twelve gigabytes appeared in Downloads."):
        r.point(changes)
    with r.say("There are also the largest files, and space by type: games, videos, archives and so on."):
        r.point(p.get_by_test_id("largest"))

    with r.say("Change alerts tell you when something important changes on the PC: an app installed, a new program run, a USB drive plugged in."):
        r.click(main_nav(p, "Activity"))
        r.point(p.locator(".event", has_text="USB drive"))
    with r.say("They also cover new Windows accounts, virus protection being switched off, and VPN or remote-control apps."):
        r.point(p.locator(".event", has_text="New program run"))
    with r.say("For each kind of alert, choose right away, a daily summary, or off, on the Notifications page."):
        r.click(main_nav(p, "Notifications"))
        r.point(p.get_by_role("heading", name="What to tell you"))
    r.title_slide("That's it", "App rules, usage, files and change alerts")
    r.narrate("That's app rules, usage, files and change alerts. The security tab has its own video.")
    w["sam"].stop()
    r.finish()


# --- 6. Parents and security ----------------------------------------------------------------------------

def test_06_parents_and_security(browser, narrator, live_app):
    w = family(live_app)
    r = recorder(browser, narrator, "06-parents-and-security", live_app)
    p = r.page
    cdp = r.ctx.new_cdp_session(p)
    cdp.send("WebAuthn.enable")
    cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {"protocol": "ctap2", "transport": "internal", "hasResidentKey": True,
                                                              "hasUserVerification": True, "isUserVerified": True, "automaticPresenceSimulation": True}})
    r.title_slide("Parents, passkeys and security", "Invite the other parent, sign in with your fingerprint, and keep the account safe")
    r.narrate("This video shows how to add the other parent, sign in with a passkey, and keep your account secure.")
    sign_in(r, w["secret"], visible=False)
    r.click(main_nav(p, "Parents and account"))
    r.uncard()
    with r.say("Everything is on the Account page. First, your name, which the other parent sees in the activity log."):
        r.point(p.get_by_label("Your name"))
    passkey = p.get_by_label("Passkey name")
    r.scroll_to(passkey)
    with r.say("A passkey lets you sign in with your fingerprint, your face, or Windows Hello, instead of a password and code. Give it a name, and add it."):
        r.type(passkey, "Alex's phone")
        r.click(p.get_by_role("button", name="Add a passkey"))
    expect(p.get_by_text("Alex's phone")).to_be_visible()
    r.narrate("Your phone asks for your fingerprint or face, and that's it. Next time, choose Sign in with a passkey.")
    invite = p.get_by_label("Email of the parent to invite")
    r.scroll_to(invite)
    with r.say("To add the other parent, type their email, and click Invite a parent."):
        r.type(invite, "jordan@example.com")
        r.click(p.get_by_role("button", name="Invite a parent"))
    link = p.get_by_test_id("invite-link")
    expect(link).to_be_visible()
    with r.say("You get a one-time link. Send it to them yourself, by message or email. It works once, and expires after two days."):
        r.point(link)
    r.narrate("They choose their own name and password, and set up their own two-factor sign-in. Both of you can then see and control every PC.")
    r.scroll_to(p.get_by_role("heading", name="Signed-in devices"))
    with r.say("Signed-in devices lists every phone and computer signed in to your account. If one looks wrong, sign it out."):
        r.point(p.get_by_role("heading", name="Signed-in devices"))
    r.steps("Built in", "Security you don't have to think about", [
        ("Two-factor sign-in is required for every parent", "A few more things work without you doing anything. Every parent needs two-factor sign-in."),
        ("<b>Remember this device</b> skips the code on your own phone for 30 days", "Tick Remember this device when you sign in, and your own phone won't ask for the code for thirty days."),
        ("Risky changes ask you to <b>confirm it's you</b> again", "Risky changes, such as deleting a PC or changing its PIN, ask you to confirm it's you, with your passkey or a code."),
        ("Repeated wrong passwords lock the account for a while", "And repeated wrong passwords lock the account for a while."),
    ], numbered=False)
    r.title_slide("Safe and shared", "Both parents, each with their own sign-in")
    r.narrate("That's it: both parents, each with their own secure sign-in.")
    w["sam"].stop()
    r.finish()


# --- 7. Real-PC checklist -----------------------------------------------------------------------------

def test_07_first_run_checklist(browser, narrator, live_app):
    r = recorder(browser, narrator, "07-first-run-checklist", live_app)
    r.title_slide("Testing on a real PC", "The first-run checklist, once, before you rely on it")
    r.narrate(
        "Kidputer Control's Windows parts can't be tested automatically, because they need a real Windows session.",
        "So, once, before you rely on it, walk through this checklist. Use a spare PC or a test account, because installing enforces the schedule.",
    )
    r.steps("Part 1", "The basics", [
        ("The PC checks in and shows <b>online</b> in the dashboard", "Start with the basics. After installing, the PC shows as online in the dashboard."),
        ("A lock time two minutes away: the <b>countdown</b> appears bottom-right", "Set a lock time two minutes away. The countdown appears in the bottom-right corner, and can't be closed."),
        ("Then Windows <b>locks</b>; signing back in shows the full-screen lock screen", "Then Windows locks. Sign back in, and the full-screen lock screen appears."),
        ("A wrong <b>PIN</b> says so; the right one unlocks and shows in the dashboard", "A wrong PIN says so. The right PIN unlocks, and the dashboard shows it."),
        ("Signed in as the <b>parent</b>: no countdown, no lock screen", "And when the parent signs in, there's no countdown and no lock screen."),
    ])
    r.steps("Part 2", "Can it be got around?", [
        ("End the lock screen in <b>Task Manager</b>: it's back within seconds", "Next, try to get around it, as your child might. End the lock screen in Task Manager. It comes back within seconds, and the dashboard logs it."),
        ("A <b>persistent message</b> can't be closed, even with Alt+F4", "A persistent message can't be closed, even with Alt F4."),
        ("<b>Lock now</b> works even during extra time", "Lock now works immediately, even during extra time."),
        ("<b>Unplug the network</b>: the bedtime lock still happens", "Unplug the network. The bedtime lock still happens, and the activity catches up when it's back."),
        ("Change a file in the program folder: a <b>tamper</b> alert appears", "Change one of Kidputer Control's files as an administrator. A tamper alert appears within a minute."),
    ])
    r.steps("Part 3", "Power, apps and updates", [
        ("Shut down: <b>PC shutting down</b>, then <b>PC turned off</b>, not a false alarm", "Shut the PC down normally. The log says it shut down, rather than raising a PC went silent alarm."),
        ("Sleep and wake: <b>PC woke from sleep</b>", "Put it to sleep and wake it. The log says it woke from sleep."),
        ("Open an app: it appears on the <b>Apps</b> tab; <b>Close</b> it with the warning", "Open an app. It appears on the Apps tab within seconds. Close it from there, and check your child sees the one-minute warning."),
        ("After an update to the server, the PC <b>updates itself</b>", "After the server is updated, the PC updates itself at its next check-in."),
    ])
    r.steps("Part 4", "Version 1.2", [
        ("After two days: install a program, plug in a USB stick: <b>alerts</b> arrive", "For version 1.2, wait two days while it learns what's normal. Then install a small program, or plug in a USB stick. Alerts arrive."),
        ("<b>Block</b> an app: it closes within a second, even if renamed", "Block an app on the App rules tab. It closes within about a second when opened, even if its file is renamed."),
        ("<b>Usage</b>: idle time isn't counted", "Use a couple of apps, then leave the PC idle. The Usage tab counts the apps, but not the idle time."),
        ("<b>Files</b>: scan C:, games stay smooth, folder sizes appear", "And on the Files tab, scan drive C. Games stay smooth while it runs, and folder sizes appear afterwards."),
    ])
    r.steps("Part 5", "Security tab", [
        ("Download the harmless <b>EICAR</b> test file: a <b>Threat found</b> alert arrives", "For the security tab, download the EICAR test file. It's harmless, but every antivirus treats it as a virus. A threat found alert should arrive within a couple of minutes."),
        ("<b>Quick scan</b>: the tab says it's running, then the time updates", "Click quick scan. The tab says it's running, and when it finishes, the last quick scan time updates."),
        ("<b>Check now</b> and <b>Install now</b>: updates install", "Click check now, then install now if anything is waiting. The activity log says the updates installed."),
        ("With a restart due, the bedtime <b>countdown says so</b>, and the PC restarts", "With a restart due, the bedtime countdown warns your child, and twenty minutes into the lock, the PC restarts."),
    ])
    r.steps("If something's wrong", "Note it down", [
        ("What you did, and what you expected", "If anything doesn't work as described, note what you did, and what you expected to happen."),
        ("The time it happened", "Note the time."),
        ("The log: <code>%ProgramData%\\KidputerControl\\logs</code>", "And the log files, in the Kidputer Control folder under Program Data. With those, the problem can be found and fixed."),
    ], numbered=False, note="The full checklist, with exact details, is in agent/README.md.")
    r.title_slide("Done once, done", "After this, Kidputer Control looks after itself")
    r.narrate("The full checklist, with every detail, is in the agent's read-me file. You only need to do it once.")
    r.finish()


# --- 8. Developers ---------------------------------------------------------------------------------------

def test_08_for_developers(browser, narrator, live_app):
    r = recorder(browser, narrator, "08-for-developers", live_app)
    r.title_slide("For developers", "How Kidputer Control is built, tested and released")
    r.narrate("This one is for whoever works on the code: how the project is built, tested and released.")
    r.slide("""<div class='slide'><p class='eyebrow'>Three parts</p><h1>How it's built</h1>
      <div class='diagram'>
        <div class='box'>web/<small>React + TypeScript dashboard</small></div>
        <div class='box green'>server/<small>FastAPI + Postgres API</small></div>
        <div class='box'>agent/<small>PowerShell on the child's PC</small></div>
      </div></div>""")
    r.reveal(0)
    with r.say("The dashboard in the web folder is React and TypeScript. The API serves it, on the same address."):
        pass
    r.reveal(1)
    with r.say("The server folder is a FastAPI app with a Postgres database. Migrations run automatically at start-up."):
        pass
    r.reveal(2)
    r.narrate("And the agent folder is the Windows side: a watchdog running as SYSTEM, and a small app in the child's session for the countdown, the lock screen and messages.")
    r.steps("Workflow", "Branches and releases", [
        ("Work happens on <b>DEV</b>", "All work happens on the DEV branch."),
        ("<b>Main</b> is what Render runs", "Main is what Render deploys, so nothing goes straight to Main."),
        ("A pull request from DEV to Main, once CI is green", "A release is a pull request from DEV into Main, merged once the automatic checks pass."),
        ("Docs move with the code; regenerate <code>README.pdf</code> with the README", "Documentation is updated in the same change as the code, and the README's PDF copy is regenerated with it."),
    ])
    r.steps("Locally", "Running it", [
        ("<code>docker compose up --build</code> &rarr; localhost:8000", "To run the whole thing locally, use docker compose. The dashboard and API come up on port eight thousand."),
        ("<code>cd web &amp;&amp; npm run dev</code> for the dashboard with hot reload", "For dashboard work, npm run dev reloads as you edit."),
    ], numbered=False)
    r.steps("Tests", "What CI checks", [
        ("<code>server/tests</code>: pytest against a real Postgres", "The server tests run against a real Postgres database."),
        ("Browser tests drive the built dashboard in Chromium, at desktop and phone width", "Browser tests drive the built dashboard in Chromium, at desktop and phone sizes, and fail if anything overflows on a phone."),
        ("<code>npm test</code>: dashboard unit tests and the type check", "The dashboard has its own unit tests and a type check."),
        ("Agent tests on Windows under PowerShell 5.1 and 7", "And the agent tests run on Windows, under both PowerShell five point one and seven."),
    ])
    r.steps("Traps", "Worth knowing", [
        ("Agent files: plain ASCII, PowerShell 5.1 syntax, C# 5", "A few traps. The agent's files must be plain ASCII and work in PowerShell five point one."),
        ("The dashboard has a strict Content-Security-Policy: no inline scripts or styles", "The dashboard has a strict security policy: no inline scripts or styles."),
        ("Never read file contents, log keys or take screenshots on the child's PC", "And by design, nothing ever reads the child's files, logs keystrokes, or takes screenshots."),
    ], numbered=False)
    r.steps("These videos", "Re-recording them", [
        ("<code>KC_VIDEOS_DIR=../videos pytest tests/test_videos.py</code>", "These videos are recorded from the real dashboard by a test file, so they can be re-recorded after the design changes."),
        ("Narration is generated offline (Kokoro voice); see <code>docs/VIDEOS.md</code>", "The narration is generated offline. The steps are in the videos document in the docs folder."),
    ], numbered=False)
    r.title_slide("Happy hacking", "Start with CLAUDE.md and docs/ARCHITECTURE.md")
    r.narrate("A good place to start is the CLAUDE file, and the architecture document. Thanks for watching.")
    r.finish()


# --- 9. Security tab -------------------------------------------------------------------------------------

def test_09_security(browser, narrator, live_app):
    w = family(live_app, threat=True, updates=True)
    r = recorder(browser, narrator, "09-security", live_app)
    p = r.page
    r.title_slide("The Security tab", "Virus protection and Windows updates")
    r.narrate("This video shows the security tab: your child's virus protection, and Windows updates.")
    sign_in(r, w["secret"], visible=False)
    card = p.locator(".device-card", has_text="Sam's PC")
    expect(card.locator(".pc-card-security")).to_contain_text("Threat needs action")
    r.uncard()
    with r.say("When something needs your attention, the PC's card on the home page says so. Here, Microsoft Defender found a threat it couldn't remove."):
        r.point(card.locator(".pc-card-security"))
    with r.say("Tap that line to open the security tab."):
        r.click(card.locator(".pc-card-security"))
    expect(p.get_by_test_id("virus-status")).to_be_visible()

    with r.say("On the left is virus protection: whether it's on, how up to date the virus definitions are, and when the PC was last scanned."):
        r.point(p.get_by_test_id("virus-status"))
    threats = p.get_by_test_id("threats")
    r.scroll_to(threats)
    with r.say("Below that is every threat Defender has found, with what it did about it. This older one was quarantined: locked away where it can't do harm."):
        r.point(threats.locator(".list-row", has_text="Presenoker"))
    with r.say("This new one couldn't be removed automatically. Remove threats asks the PC to try again."):
        r.point(threats.locator(".list-row", has_text="Wacatac"))
        r.click(p.get_by_role("button", name="Remove threats"))
    expect(threats.locator(".list-row", has_text="Wacatac")).to_contain_text("Removed", timeout=20000)
    r.narrate("A few seconds later, it's removed.")
    r.scroll_top()
    with r.say("Quick scan checks the places viruses usually hide, in about five to fifteen minutes. Full scan checks everything, and takes longer."):
        r.click(p.get_by_role("button", name="Quick scan"))
    expect(p.get_by_test_id("security-activity")).to_be_visible(timeout=15000)
    with r.say("While a scan runs, the tab says so. Your child can keep using the PC."):
        r.point(p.get_by_test_id("security-activity"))
    expect(p.get_by_test_id("security-activity")).to_be_hidden(timeout=20000)

    updates = p.get_by_test_id("pending-updates")
    with r.say("On the right are Windows updates: the Windows version, what's waiting to install, and when updates last installed."):
        r.point(updates)
    with r.say("Updates install by themselves, but Install now does it straight away."):
        r.click(p.get_by_role("button", name="Install now"))
    expect(p.get_by_test_id("update-status")).to_contain_text("Restart needed", timeout=25000)
    with r.say("Most updates need a restart to finish. The PC does that at bedtime, twenty minutes after it locks, so it never interrupts your child. That evening's countdown tells them to save their work."):
        r.point(p.get_by_test_id("update-status"))

    settings = p.get_by_role("heading", name="Security settings")
    r.scroll_to(settings)
    with r.say("In the security settings, choose whether updates install automatically, or are only shown to you."):
        r.point(p.get_by_label("Install Windows updates automatically"))
    with r.say("The big yearly Windows upgrades are left out unless you tick this."):
        r.point(p.get_by_label("Also install new Windows versions (the big yearly upgrades)"))
    with r.say("On Windows Pro, this stops your child pausing updates. Windows Home doesn't allow that, so you get an alert instead if they're paused."):
        r.point(p.get_by_text("Stop my child pausing Windows updates"))
    with r.say("And a full virus scan runs every week at bedtime."):
        r.point(p.get_by_text("Full virus scan every week, at bedtime"))

    with r.say("You're alerted when a threat is found, straight away, and about failed updates, in the daily summary. Change either on the notifications page."):
        r.click(main_nav(p, "Notifications"))
        r.scroll_to(p.get_by_text("Viruses and threats"))
        r.point(p.get_by_text("Viruses and threats"))
    r.title_slide("Protected and up to date", "Virus protection and Windows updates, looked after")
    r.narrate("That's the security tab: virus protection and Windows updates, looked after for you.")
    w["sam"].stop()
    r.finish()
