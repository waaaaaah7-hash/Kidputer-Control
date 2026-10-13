"""Parent notifications (roadmap #10): queueing, grouping, quiet hours, digests, push, and "PC went silent"."""

import base64
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select, update

from app import notify
from app.config import Settings
from app.db import SessionLocal
from app.jobs import run_jobs_once
from app.models import ActivityEvent, Device, Notification, NotificationSettings, PushSubscription

from .conftest import make_admin

EMAIL_SETTINGS = Settings(smtp_host="smtp.test", smtp_from="kc@example.com", public_url="https://kc.example.com")
N = "/api/v1/notifications"


SEND_DIGESTS = notify.send_digests


@pytest.fixture(autouse=True)
def no_digests(monkeypatch):
    """Daily summaries go out at a clock hour, so they'd make other tests depend on the time of day.
    Only the digest test turns them back on."""
    monkeypatch.setattr(notify, "send_digests", lambda db, settings, now: 0)


@pytest.fixture
def outbox(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(notify, "send_email", lambda settings, to, subject, text: sent.append({"to": to, "subject": subject, "text": text}))
    return sent


def ev(event_type: str, message: str = "something", data: dict | None = None, at: datetime | None = None) -> dict:
    return {
        "client_event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "severity": "info",
        "message": message,
        "data": data,
        "occurred_at": (at or datetime.now(timezone.utc)).isoformat(),
    }


def post(agent, *events):
    r = agent.post("/api/v1/agent/events", json={"events": list(events)})
    assert r.status_code == 200, r.text


def notifications() -> list[Notification]:
    with SessionLocal() as db:
        return list(db.scalars(select(Notification).order_by(Notification.id)))


def run(now: datetime | None = None, settings: Settings = EMAIL_SETTINGS) -> None:
    assert run_jobs_once(settings, now)


def later(**kw) -> datetime:
    return datetime.now(timezone.utc) + timedelta(**kw)


# --- Queueing ------------------------------------------------------------------------------------------


def test_events_queue_at_each_category_level(agent):
    make_admin("second@example.com")
    post(agent, ev("tamper.detected", "Installed files changed"), ev("app.blocked", "Roblox"), ev("device.back_online"), ev("session.locked"))
    rows = notifications()
    assert sorted((n.category, n.level) for n in rows) == [
        ("blocked_apps", "digest"), ("blocked_apps", "digest"), ("tamper", "immediate"), ("tamper", "immediate"),
    ]
    tamper = next(n for n in rows if n.category == "tamper")
    assert tamper.title == "Kid PC: Tamper alert" and tamper.body == "Installed files changed"
    assert tamper.url == f"/devices/{agent.device_id}"


def test_duplicate_agent_events_notify_once(agent):
    e = ev("tamper.detected")
    post(agent, e)
    post(agent, e)  # the agent retried an upload
    assert len(notifications()) == 1


def test_parents_choose_levels(admin_client, agent):
    s = admin_client.get(f"{N}/settings").json()
    assert s["levels"]["tamper"] == "immediate" and s["levels"]["usb"] == "digest" and s["levels"]["power"] == "off"
    assert s["email_available"] is False and s["vapid_public_key"]
    body = {k: s[k] for k in ("email_enabled", "push_enabled", "timezone", "digest_hour")}
    body["levels"] = {"tamper": "off", "usb": "immediate"}
    r = admin_client.put(f"{N}/settings", json=body)
    assert r.status_code == 200 and r.json()["levels"]["tamper"] == "off" and r.json()["levels"]["installs"] == "immediate"
    post(agent, ev("tamper.detected"), ev("usb.connected", "SANDISK 32GB"))
    assert [(n.category, n.level) for n in notifications()] == [("usb", "immediate")]

    assert admin_client.put(f"{N}/settings", json={**body, "levels": {"nope": "off"}}).status_code == 422
    assert admin_client.put(f"{N}/settings", json={**body, "levels": {"usb": "loud"}}).status_code == 422
    assert admin_client.put(f"{N}/settings", json={**body, "timezone": "Mars/Base"}).status_code == 422


# --- Sending -----------------------------------------------------------------------------------------


def test_immediate_alerts_wait_briefly_then_group(agent, outbox):
    post(agent, *[ev("archive.new", f"game{i}.zip") for i in range(3)], ev("software.installed", "Steam"))
    run()
    assert outbox == []  # gathering a burst first
    run(later(seconds=25))
    subjects = sorted(m["subject"] for m in outbox)
    assert subjects == ["Kid PC: App installed"]  # archives are digest-only by default
    assert "Steam" in outbox[0]["text"] and f"https://kc.example.com/devices/{agent.device_id}" in outbox[0]["text"]

    outbox.clear()
    post(agent, *[ev("software.installed", f"App {i}") for i in range(7)])
    run(later(seconds=25))
    assert outbox == []  # this group alerted moments ago: collect for longer
    run(later(minutes=6))
    assert [m["subject"] for m in outbox] == ["Kid PC: 7 app changes"]
    assert "...and 2 more" in outbox[0]["text"]


def no_silence_alerts(admin_client, agent) -> None:
    """For tests that jump the clock ahead by days, which would otherwise make the PC look silent."""
    admin_client.patch(f"/api/v1/devices/{agent.device_id}/settings", json={"silent_alert_minutes": 0})


def test_quiet_hours_hold_alerts_until_they_end(admin_client, agent, outbox):
    no_silence_alerts(admin_client, agent)
    admin_client.get(f"{N}/settings")  # creates the parent's settings
    with SessionLocal() as db:
        db.execute(update(NotificationSettings).values(quiet_start=time(22, 0), quiet_end=time(7, 0), timezone="UTC"))
        db.commit()
    post(agent, ev("tamper.detected"))
    night = datetime.now(timezone.utc).replace(hour=23, minute=0) + timedelta(days=1)
    run(night)
    assert outbox == []
    run(night.replace(hour=7, minute=5) + timedelta(days=1))
    assert len(outbox) == 1


def test_daily_digest_at_the_chosen_hour(admin_client, agent, outbox, monkeypatch):
    monkeypatch.setattr(notify, "send_digests", SEND_DIGESTS)
    no_silence_alerts(admin_client, agent)
    admin_client.get(f"{N}/settings")
    with SessionLocal() as db:
        db.execute(update(NotificationSettings).values(digest_hour=18, timezone="UTC"))
        db.commit()
    post(agent, ev("usb.connected", "SANDISK"), ev("app.blocked", "Roblox"), ev("app.blocked", "Fortnite"))
    day = datetime.now(timezone.utc).replace(hour=17, minute=0) + timedelta(days=1)
    run(day)
    assert outbox == []
    run(day.replace(hour=18, minute=1))
    assert len(outbox) == 1
    assert outbox[0]["subject"] == "Kidputer Control daily summary: 1 USB drives, 2 blocked apps"
    assert "Roblox" in outbox[0]["text"] and "Fortnite" in outbox[0]["text"]
    run(day.replace(hour=20))
    assert len(outbox) == 1  # once a day


def test_removed_parents_get_nothing(admin_client, agent, outbox):
    other, _ = make_admin("second@example.com")
    post(agent, ev("tamper.detected"))
    from app.models import AdminUser

    with SessionLocal() as db:
        db.execute(update(AdminUser).where(AdminUser.id == other.id).values(disabled_at=datetime.now(timezone.utc)))
        db.commit()
    run(later(seconds=25))
    assert [m["to"] for m in outbox] == ["parent@example.com"]


def test_email_off_or_unconfigured_sends_no_email(admin_client, agent, outbox):
    post(agent, ev("tamper.detected"))
    run(later(seconds=25), Settings())
    assert outbox == []
    assert all(n.sent_at is not None for n in notifications())


# --- Push ----------------------------------------------------------------------------------------------


class FakePushService:
    def __init__(self, status: int = 201):
        self.status = status
        self.requests: list[dict] = []

    def post(self, url, data=None, headers=None, timeout=None):
        self.requests.append({"url": url, "data": data, "headers": headers})
        status = self.status

        class R:
            status_code = status
            reason = "Gone" if status == 410 else "OK"
            headers: dict = {}
            text = ""
            content = b""

        return R()


def browser_keys() -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    return {"p256dh": b64(raw), "auth": b64(b"0123456789abcdef")}


def test_push_subscription_and_encrypted_delivery(admin_client, agent, monkeypatch, outbox):
    service = FakePushService()
    monkeypatch.setattr(notify, "push_session", service)
    endpoint = "https://push.example.test/send/abc"
    assert admin_client.post(f"{N}/push-subscriptions", json={"endpoint": endpoint, "keys": browser_keys()}).status_code == 204
    assert admin_client.get(f"{N}/settings").json()["push_devices"] == 1
    assert admin_client.post(f"{N}/push-subscriptions", json={"endpoint": "http://insecure", "keys": browser_keys()}).status_code == 422

    r = admin_client.post(f"{N}/test")
    assert r.status_code == 200 and r.json()["push"] == 1
    sent = service.requests[0]
    assert sent["url"] == endpoint
    assert sent["headers"]["content-encoding"] == "aes128gcm"
    assert sent["headers"]["authorization"].startswith("vapid t=")
    assert b"Notifications are working" not in sent["data"]  # end-to-end encrypted for the browser

    service.status = 410  # the parent removed the app from their phone
    post(agent, ev("tamper.detected"))
    run(later(seconds=25))
    with SessionLocal() as db:
        assert db.scalar(select(PushSubscription)) is None


def test_unsubscribe_and_test_needs_a_channel(admin_client, monkeypatch):
    monkeypatch.setattr(notify, "push_session", FakePushService())
    endpoint = "https://push.example.test/send/xyz"
    admin_client.post(f"{N}/push-subscriptions", json={"endpoint": endpoint, "keys": browser_keys()})
    assert admin_client.post(f"{N}/push-subscriptions/remove", json={"endpoint": endpoint}).status_code == 204
    assert admin_client.get(f"{N}/settings").json()["push_devices"] == 0
    s = admin_client.get(f"{N}/settings").json()
    body = {k: s[k] for k in ("timezone", "digest_hour", "levels")} | {"email_enabled": False, "push_enabled": False}
    admin_client.put(f"{N}/settings", json=body)
    assert admin_client.post(f"{N}/test").status_code == 400


def test_vapid_key_is_created_once():
    with SessionLocal() as db:
        a = notify.vapid_public_key(db)
        db.commit()
    with SessionLocal() as db:
        assert notify.vapid_public_key(db) == a
    assert len(base64.urlsafe_b64decode(a + "==")) == 65


# --- PC went silent ----------------------------------------------------------------------------------------


def set_last_seen(device_id: str, when: datetime) -> None:
    with SessionLocal() as db:
        db.execute(update(Device).where(Device.id == uuid.UUID(device_id)).values(last_seen_at=when))
        db.commit()


def system_events(device_id: str) -> list[tuple[str, str]]:
    with SessionLocal() as db:
        rows = db.scalars(
            select(ActivityEvent)
            .where(
                ActivityEvent.device_id == uuid.UUID(device_id),
                ActivityEvent.source == "system",
                ActivityEvent.event_type != "device.enrolled",
            )
            .order_by(ActivityEvent.id)
        )
        return [(e.event_type, e.severity) for e in rows]


def test_silent_pc_alerts_once_then_reports_it_was_off(admin_client, agent, outbox):
    agent.get("/api/v1/agent/state")
    silent_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    set_last_seen(agent.device_id, silent_at)
    run()
    run()
    assert system_events(agent.device_id) == [("device.silent", "critical")]
    run(later(seconds=25))
    assert [m["subject"] for m in outbox] == ["Kid PC: No contact"]
    assert "may be turned off" in outbox[0]["text"]

    # Back: Windows was started after the silence began, so the PC was simply off.
    agent.get("/api/v1/agent/state")
    post(agent, ev("agent.started", data={"boot_time": (silent_at + timedelta(minutes=10)).isoformat()}))
    assert system_events(agent.device_id) == [("device.silent", "critical"), ("device.back_online", "info")]


def test_agent_stopped_while_windows_ran_is_a_tamper_alert(admin_client, agent, outbox):
    silent_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    set_last_seen(agent.device_id, silent_at)
    run()
    agent.get("/api/v1/agent/state")
    post(agent, ev("agent.started", data={"boot_time": (silent_at - timedelta(hours=3)).isoformat()}))
    assert system_events(agent.device_id)[-1] == ("device.agent_was_stopped", "critical")
    run(later(minutes=1))
    assert "Kid PC: Kidputer Control was stopped" in [m["subject"] for m in outbox]


def test_sleep_is_not_a_stopped_agent(agent):
    silent_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    set_last_seen(agent.device_id, silent_at)
    run()
    agent.get("/api/v1/agent/state")
    post(agent, ev("pc.resumed"), ev("agent.started", data={"boot_time": (silent_at - timedelta(hours=3)).isoformat()}, at=later(seconds=5)))
    assert "device.agent_was_stopped" not in [t for t, _ in system_events(agent.device_id)]


def test_announced_shutdown_is_not_an_alarm(agent):
    post(agent, ev("pc.shutting_down"))
    set_last_seen(agent.device_id, datetime.now(timezone.utc) - timedelta(minutes=20))
    run()
    assert system_events(agent.device_id) == [("device.offline", "info")]


def test_silent_alerts_can_be_turned_off_per_pc(admin_client, agent):
    assert admin_client.patch(f"/api/v1/devices/{agent.device_id}/settings", json={"silent_alert_minutes": 0}).status_code == 200
    set_last_seen(agent.device_id, datetime.now(timezone.utc) - timedelta(hours=2))
    run()
    assert system_events(agent.device_id) == []


def test_recent_notifications_list(admin_client, agent):
    post(agent, ev("tamper.detected", "files changed"))
    items = admin_client.get(N).json()
    assert [(i["category"], i["body"]) for i in items] == [("tamper", "files changed")]


def test_settings_page_only_offers_alerts_that_exist_yet(admin_client):
    shown = [c["id"] for c in admin_client.get(f"{N}/settings").json()["categories"]]
    assert shown == ["silent", "tamper", "installs", "first_run", "vpn", "accounts", "defender", "archives", "usb", "pin", "power"]


def test_change_monitoring_events_reach_parents(agent):
    post(
        agent,
        ev("software.installed", "Installed: Steam (Valve)"),
        ev("program.first_run", "First run of Cool Game straight from a zip or other archive"),
        ev("software.vpn_detected", "VPN installed: NordVPN"),
        ev("account.admin_added", "Sam is now an administrator"),
        ev("defender.disabled", "Windows Defender real-time protection was switched off"),
        ev("usb.connected", "SANDISK (E:, 29.8 GB) connected: 3 programs, 2 archives"),
        ev("archive.new", "New archive: mods.zip (12 MB)"),
    )
    got = sorted((n.category, n.level) for n in notifications())
    assert got == [
        ("accounts", "immediate"), ("archives", "digest"), ("defender", "immediate"), ("first_run", "immediate"),
        ("installs", "immediate"), ("usb", "digest"), ("vpn", "immediate"),
    ]
