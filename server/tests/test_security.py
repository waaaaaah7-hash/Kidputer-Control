"""Security tab (roadmap #23): virus protection and Windows updates."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Notification
from app.pc_security import summary

NOW = datetime(2026, 1, 20, 12, 0, tzinfo=timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def report(**over) -> dict:
    """A healthy PC: Defender on and current, nothing waiting."""
    r = {
        "defender": {
            "available": True, "antivirus_enabled": True, "realtime": True, "tamper_protected": True,
            "signatures_updated": iso(NOW - timedelta(hours=5)), "signature_version": "1.443.12.0",
            "quick_scan_at": iso(NOW - timedelta(days=1)), "full_scan_at": iso(NOW - timedelta(days=4)),
            "threats": [], "other_antivirus": [],
        },
        "updates": {
            "windows": {"name": "Windows 11 Home", "version": "24H2", "build": "26100.4652"},
            "reboot_required": False, "reboot_required_since": None, "paused_until": None, "service_disabled": False,
            "last_search_at": iso(NOW - timedelta(hours=3)), "last_installed_at": iso(NOW - timedelta(days=3)),
            "pending": [], "last_check_at": iso(NOW - timedelta(hours=3)), "last_install": None,
        },
        "running": None,
        "queued": [],
    }
    for path, value in over.items():
        section, key = path.split("__")
        r[section][key] = value
    return r


# --- The one-line summary -------------------------------------------------------------------------------


def test_summary_healthy_pc():
    s = summary(report(), NOW, NOW)
    assert s == {"level": "ok", "text": "Protected and up to date", "items": []}
    assert summary(None, None, NOW) is None
    assert summary(report(), NOW - timedelta(days=4), NOW)["level"] == "unknown"


def test_summary_threats():
    found = {"id": "{A}", "name": "Trojan:Win32/Wacatac.B!ml", "severity": "severe", "status": "remove_failed", "needs_action": True,
             "detected_at": iso(NOW - timedelta(hours=1)), "files": ["C:\\Users\\Sam\\Downloads\\x.exe"]}
    s = summary(report(defender__threats=[found]), NOW, NOW)
    assert s["level"] == "attention" and s["text"] == "Threat needs action: Trojan:Win32/Wacatac.B!ml"
    handled = dict(found, status="quarantined", needs_action=False)
    s = summary(report(defender__threats=[handled]), NOW, NOW)
    assert s["level"] == "ok" and s["items"] == [{"level": "info", "text": "Trojan:Win32/Wacatac.B!ml was quarantined"}]
    old = dict(handled, detected_at=iso(NOW - timedelta(days=30)))
    assert summary(report(defender__threats=[old]), NOW, NOW)["items"] == []


def test_summary_protection_problems():
    assert summary(report(defender__realtime=False), NOW, NOW)["text"] == "Virus protection is switched off"
    s = summary(report(defender__signatures_updated=iso(NOW - timedelta(days=5))), NOW, NOW)
    assert s["text"] == "Virus definitions are 5 days old"
    # Another antivirus took over from Defender.
    norton = {"name": "Norton 360", "enabled": True, "up_to_date": True}
    assert summary(report(defender__antivirus_enabled=False, defender__other_antivirus=[norton]), NOW, NOW)["level"] == "ok"
    s = summary(report(defender__antivirus_enabled=False, defender__other_antivirus=[dict(norton, up_to_date=False)]), NOW, NOW)
    assert s["text"] == "Norton 360 is out of date"
    assert summary(report(defender__available=False), NOW, NOW)["text"] == "No virus protection found"
    s = summary(report(defender__quick_scan_at=iso(NOW - timedelta(days=20)), defender__full_scan_at=None), NOW, NOW)
    assert s["text"] == "No virus scan for 20 days"
    assert summary(report(defender__quick_scan_at=iso(NOW - timedelta(days=20))), NOW, NOW)["level"] == "ok"  # the full scan was recent


def test_summary_windows_updates():
    s = summary(report(updates__paused_until=iso(NOW + timedelta(days=5)), updates__service_disabled=True), NOW, NOW)
    assert s["text"] == "Windows Update is switched off (+1 more)"
    s = summary(report(updates__reboot_required=True, updates__reboot_required_since=iso(NOW - timedelta(hours=10))), NOW, NOW)
    assert s["level"] == "ok" and s["items"][0]["text"] == "A restart will finish installing updates"
    s = summary(report(updates__reboot_required=True, updates__reboot_required_since=iso(NOW - timedelta(days=4))), NOW, NOW)
    assert s["text"] == "A restart has been needed for 4 days to finish updates"
    waiting = [{"title": "2026-01 Cumulative Update", "kind": "security"}, {"title": "Windows 11, version 25H2", "kind": "feature"}]
    s = summary(report(updates__pending=waiting), NOW, NOW)
    assert s["level"] == "ok" and s["items"] == [{"level": "info", "text": "1 Windows update waiting"}]
    s = summary(report(updates__pending=waiting, updates__last_installed_at=iso(NOW - timedelta(days=40))), NOW, NOW)
    assert s["text"] == "1 Windows update waiting"
    failed = {"at": iso(NOW - timedelta(hours=2)), "installed": [], "failed": ["2026-01 Cumulative Update"], "message": None}
    assert summary(report(updates__last_install=failed), NOW, NOW)["text"] == "Windows updates failed to install"


# --- API --------------------------------------------------------------------------------------------------


def test_security_report_and_summary_on_the_pc_list(admin_client, agent):
    dev = agent.device_id
    r = admin_client.get(f"/api/v1/devices/{dev}/security").json()
    assert r == {"updated_at": None, "report": None, "summary": None, "commands": []}
    assert admin_client.get("/api/v1/devices").json()[0]["security"] is None

    now = datetime.now(timezone.utc)
    rep = report(defender__signatures_updated=iso(now - timedelta(hours=2)), defender__quick_scan_at=iso(now - timedelta(days=1)))
    assert agent.post("/api/v1/agent/reports/security", json=rep).status_code == 204
    r = admin_client.get(f"/api/v1/devices/{dev}/security").json()
    assert r["report"]["updates"]["windows"]["version"] == "24H2"
    assert r["summary"]["level"] == "ok"
    assert admin_client.get("/api/v1/devices").json()[0]["security"]["text"] == "Protected and up to date"

    big = {"defender": {"threats": [{"name": "x" * 1000}] * 300}}
    assert agent.post("/api/v1/agent/reports/security", json=big).status_code == 413


def test_security_actions_reach_the_pc(admin_client, agent):
    dev = agent.device_id
    r = admin_client.post(f"/api/v1/devices/{dev}/security/quick_scan")
    assert r.status_code == 201 and r.json()["kind"] == "security" and r.json()["payload"] == {"action": "quick_scan"}
    assert admin_client.post(f"/api/v1/devices/{dev}/security/format_disk").status_code == 422
    admin_client.post(f"/api/v1/devices/{dev}/security/install_updates")
    cmds = agent.get("/api/v1/agent/state").json()["commands"]
    assert [(c["kind"], c["payload"]["action"]) for c in cmds] == [("security", "quick_scan"), ("security", "install_updates")]
    # The PC finishes the scan.
    agent.post(f"/api/v1/agent/commands/{cmds[0]['id']}/result", json={"status": "done", "result": {"minutes": 7}})
    listed = admin_client.get(f"/api/v1/devices/{dev}/security").json()["commands"]
    assert [(c["payload"]["action"], c["status"]) for c in listed] == [("install_updates", "pending"), ("quick_scan", "done")]
    events = admin_client.get("/api/v1/events", params={"device_id": dev}).json()
    assert any(e["event_type"] == "security.requested" and e["message"] == "Asked for a quick virus scan" for e in events)


def test_security_settings_go_to_the_pc(admin_client, agent):
    dev = agent.device_id
    s = admin_client.get(f"/api/v1/devices/{dev}/settings").json()
    assert (s["updates_mode"], s["feature_updates"], s["restart_for_updates"], s["weekly_full_scan"], s["block_pause_updates"]) == (
        "install", False, True, True, True)
    assert agent.get("/api/v1/agent/state").json()["security"] == {
        "updates_mode": "install", "feature_updates": False, "restart_for_updates": True, "weekly_full_scan": True, "block_pause_updates": True,
    }
    r = admin_client.patch(f"/api/v1/devices/{dev}/settings", json={"updates_mode": "report", "feature_updates": True, "weekly_full_scan": False,
                                                                     "block_pause_updates": False})
    assert r.status_code == 200, r.text
    state = agent.get("/api/v1/agent/state").json()["security"]
    assert state["updates_mode"] == "report" and state["feature_updates"] is True and state["weekly_full_scan"] is False
    assert state["block_pause_updates"] is False
    assert admin_client.patch(f"/api/v1/devices/{dev}/settings", json={"updates_mode": "never"}).status_code == 422


def test_threat_alerts_open_the_security_tab(agent):
    def ev(event_type, message):
        return {"client_event_id": str(uuid.uuid4()), "event_type": event_type, "severity": "critical", "message": message,
                "data": None, "occurred_at": datetime.now(timezone.utc).isoformat()}

    r = agent.post("/api/v1/agent/events", json={"events": [
        ev("defender.threat_found", "Trojan:Win32/Wacatac.B!ml found in C:\\Users\\Sam\\Downloads\\x.exe; Defender quarantined it"),
        ev("windows_update.failed", "Windows updates failed to install: 2026-01 Cumulative Update"),
    ]})
    assert r.status_code == 200, r.text
    with SessionLocal() as db:
        rows = list(db.scalars(select(Notification).order_by(Notification.id)))
    assert [(n.category, n.level, n.title) for n in rows] == [
        ("threats", "immediate", "Kid PC: Threat found"),
        ("windows_updates", "digest", "Kid PC: Updates failed"),
    ]
    assert rows[0].url == f"/devices/{agent.device_id}?tab=security"
