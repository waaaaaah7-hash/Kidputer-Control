"""Running apps (roadmap #1), closing them (#2), and the command/report plumbing they use."""

from datetime import timedelta

from sqlalchemy import update

from app.db import SessionLocal
from app.deps import utcnow
from app.models import DeviceCommand

PROCS = {
    "processes": [
        {"pid": 4242, "name": "javaw", "path": "C:\\Program Files\\Minecraft\\javaw.exe", "title": None, "has_window": True, "memory_mb": 900},
        {"pid": 77, "name": "OneDrive", "path": "C:\\Users\\Sam\\AppData\\Local\\OneDrive.exe", "has_window": False, "memory_mb": 40},
    ]
}


def test_process_report_round_trip(admin_client, agent):
    dev = agent.device_id
    assert admin_client.get(f"/api/v1/devices/{dev}/processes").json() == {"updated_at": None, "processes": [], "window_titles": False}
    assert agent.post("/api/v1/agent/reports/processes", json=PROCS).status_code == 204
    r = admin_client.get(f"/api/v1/devices/{dev}/processes").json()
    assert r["updated_at"] and [p["pid"] for p in r["processes"]] == [4242, 77]


def test_reports_are_checked(agent, admin_client):
    assert agent.post("/api/v1/agent/reports/secrets", json={}).status_code == 404
    assert agent.post("/api/v1/agent/reports/processes", content=b"[1,2]").status_code == 422
    assert agent.post("/api/v1/agent/reports/processes", content=b"not json").status_code == 422
    big = {"processes": [{"name": "x" * 1000}] * 600}
    assert agent.post("/api/v1/agent/reports/processes", json=big).status_code == 413
    admin_client.headers.pop("Authorization", None)
    assert admin_client.post("/api/v1/agent/reports/processes", json=PROCS).status_code == 401


def test_live_mode_reaches_the_agent(admin_client, agent):
    assert agent.get("/api/v1/agent/state").json()["live_until"] is None
    assert admin_client.post(f"/api/v1/devices/{agent.device_id}/live").status_code == 204
    live = agent.get("/api/v1/agent/state").json()["live_until"]
    assert live is not None and live.endswith(("00+00:00", "30+00:00"))  # rounded, so the ETag is stable


def test_stop_app_command_flow(admin_client, agent):
    dev = agent.device_id
    r = admin_client.post(f"/api/v1/devices/{dev}/apps/stop", json={"path": PROCS["processes"][0]["path"], "name": "Minecraft"})
    assert r.status_code == 201 and r.json()["status"] == "pending" and r.json()["payload"]["grace_seconds"] == 60
    cid = r.json()["id"]
    cmds = agent.get("/api/v1/agent/state").json()["commands"]
    assert [(c["id"], c["kind"]) for c in cmds] == [(cid, "stop_app")]

    assert agent.post(f"/api/v1/agent/commands/{cid}/result", json={"status": "done", "result": {"closed": 1}}).status_code == 204
    # A retried upload of the same result changes nothing.
    assert agent.post(f"/api/v1/agent/commands/{cid}/result", json={"status": "failed"}).status_code == 204
    got = admin_client.get(f"/api/v1/devices/{dev}/commands/{cid}").json()
    assert got["status"] == "done" and got["result"] == {"closed": 1}
    assert agent.get("/api/v1/agent/state").json()["commands"] == []
    events = admin_client.get("/api/v1/events", params={"event_type": "app.stop_requested"}).json()
    assert events[0]["message"] == "Asked to close Minecraft after a 60-second warning"


def test_expired_commands_are_dropped(admin_client, agent):
    dev = agent.device_id
    cid = admin_client.post(f"/api/v1/devices/{dev}/apps/stop", json={"path": "C:\\x.exe", "name": "X", "grace_seconds": 0}).json()["id"]
    with SessionLocal() as db:
        db.execute(update(DeviceCommand).values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert agent.get("/api/v1/agent/state").json()["commands"] == []
    assert admin_client.get(f"/api/v1/devices/{dev}/commands/{cid}").json()["status"] == "expired"


def test_commands_belong_to_their_device(admin_client, agent):
    other = admin_client.post("/api/v1/devices", json={"name": "Other"}).json()["device_id"]
    cid = admin_client.post(f"/api/v1/devices/{other}/apps/stop", json={"path": "C:\\x.exe", "name": "X"}).json()["id"]
    assert agent.post(f"/api/v1/agent/commands/{cid}/result", json={"status": "done"}).status_code == 404
    assert admin_client.get(f"/api/v1/devices/{agent.device_id}/commands/{cid}").status_code == 404
    assert admin_client.post(f"/api/v1/devices/{other}/apps/stop", json={"path": "C:\\x.exe", "name": "X", "grace_seconds": 9999}).status_code == 422


def test_window_titles_setting_reaches_the_agent(admin_client, agent):
    assert agent.get("/api/v1/agent/state").json()["show_window_titles"] is False
    admin_client.patch(f"/api/v1/devices/{agent.device_id}/settings", json={"show_window_titles": True})
    assert agent.get("/api/v1/agent/state").json()["show_window_titles"] is True
