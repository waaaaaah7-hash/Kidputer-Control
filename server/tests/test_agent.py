import base64
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app


def _event(event_type, **data):
    return {
        "client_event_id": str(uuid.uuid4()),
        "event_type": event_type,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "data": data or None,
    }


def test_enrollment_code_is_single_use(admin_client):
    code = admin_client.post("/api/v1/devices", json={"name": "Kid PC"}).json()["enrollment_code"]
    # Codes are typed by hand: lower case / missing dashes are accepted.
    r = admin_client.post("/api/v1/agent/enroll", json={"enrollment_code": code.lower().replace("-", "")})
    assert r.status_code == 200
    assert admin_client.post("/api/v1/agent/enroll", json={"enrollment_code": code}).status_code == 401


def test_state_requires_device_token(client):
    assert client.get("/api/v1/agent/state").status_code == 401
    assert client.get("/api/v1/agent/state", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_state_snapshot_and_etag(admin_client, agent):
    dev = agent.device_id
    admin_client.put(f"/api/v1/devices/{dev}/schedule", json={"entries": [{"day_of_week": 4, "lock_at": "22:00", "unlock_at": "08:00"}]})
    admin_client.put(f"/api/v1/devices/{dev}/pin", json={"pin": "4321"})

    r = agent.get("/api/v1/agent/state")
    assert r.status_code == 200
    state = r.json()
    assert state["timezone"] == "Europe/London"
    assert state["schedule"] == [{"day_of_week": 4, "lock_at": "22:00", "unlock_at": "08:00"}]
    assert state["message"] is None and state["immediate_lock"] is None and state["overrides"] == []

    # The agent can verify the PIN offline with the same PBKDF2 parameters.
    pin = state["pin"]
    derived = hashlib.pbkdf2_hmac("sha256", b"4321", base64.b64decode(pin["salt"]), pin["iterations"], dklen=32)
    assert base64.b64encode(derived).decode() == pin["hash"]

    etag = r.headers["ETag"]
    assert agent.get("/api/v1/agent/state", headers={"If-None-Match": etag}).status_code == 304

    admin_client.post(f"/api/v1/devices/{dev}/overrides", json={"duration_minutes": 30})
    r = agent.get("/api/v1/agent/state", headers={"If-None-Match": etag})
    assert r.status_code == 200 and len(r.json()["overrides"]) == 1


def test_immediate_lock_coexists_with_override(admin_client, agent):
    dev = agent.device_id
    admin_client.post(f"/api/v1/devices/{dev}/overrides", json={"duration_minutes": 60})
    lock = admin_client.post(f"/api/v1/devices/{dev}/lock", json={"reason": "chores"}).json()
    state = agent.get("/api/v1/agent/state").json()
    assert state["immediate_lock"]["id"] == lock["id"]
    assert len(state["overrides"]) == 1  # not cancelled

    agent.post("/api/v1/agent/events", json={"events": [_event("lock.applied", lock_request_id=lock["id"])]})
    assert admin_client.get(f"/api/v1/devices/{dev}/lock").json()["applied_at"] is not None


def test_events_are_idempotent(agent, admin_client):
    batch = {"events": [_event("tamper.detected", file="Runtime.ps1"), _event("session.locked")]}
    batch["events"][0]["severity"] = "critical"
    assert agent.post("/api/v1/agent/events", json=batch).json() == {"accepted": 2, "duplicates": 0}
    assert agent.post("/api/v1/agent/events", json=batch).json() == {"accepted": 0, "duplicates": 2}
    crit = admin_client.get("/api/v1/events?severity=critical").json()
    assert len(crit) == 1 and crit[0]["source"] == "agent" and crit[0]["data"] == {"file": "Runtime.ps1"}


def test_closable_message_dismissal(admin_client, agent):
    dev = agent.device_id
    msg = admin_client.post(f"/api/v1/devices/{dev}/message", json={"body": "Hi", "mode": "closable"}).json()
    assert agent.get("/api/v1/agent/state").json()["message"]["id"] == msg["id"]
    agent.post("/api/v1/agent/events", json={"events": [_event("message.displayed", message_id=msg["id"]), _event("message.dismissed", message_id=msg["id"])]})
    assert agent.get("/api/v1/agent/state").json()["message"] is None


def test_persistent_message_cannot_be_dismissed_by_agent(admin_client, agent):
    dev = agent.device_id
    msg = admin_client.post(f"/api/v1/devices/{dev}/message", json={"body": "Stay", "mode": "persistent"}).json()
    agent.post("/api/v1/agent/events", json={"events": [_event("message.dismissed", message_id=msg["id"])]})
    assert agent.get("/api/v1/agent/state").json()["message"]["id"] == msg["id"]


def test_agent_cannot_touch_other_devices_rows(admin_client, agent):
    other = admin_client.post("/api/v1/devices", json={"name": "Other"}).json()["device_id"]
    msg = admin_client.post(f"/api/v1/devices/{other}/message", json={"body": "x", "mode": "closable"}).json()
    agent.post("/api/v1/agent/events", json={"events": [_event("message.dismissed", message_id=msg["id"])]})
    assert admin_client.get(f"/api/v1/devices/{other}/message").json()["dismissed_at"] is None


def test_heartbeat_updates_status(admin_client, agent):
    r = agent.post("/api/v1/agent/heartbeat", json={"agent_version": "2.0.1", "status": {"locked": True, "ui_agent_alive": True}})
    assert r.status_code == 204
    d = admin_client.get(f"/api/v1/devices/{agent.device_id}").json()
    assert d["enrolled"] is True and d["agent_version"] == "2.0.1" and d["last_status"]["locked"] is True


def test_reissuing_enrollment_code_revokes_token(admin_client, agent):
    admin_client.post(f"/api/v1/devices/{agent.device_id}/enrollment-code")
    assert agent.get("/api/v1/agent/state").status_code == 401


def test_agent_token_is_not_an_admin_session(agent):
    assert agent.get("/api/v1/devices").status_code == 401


def test_healthz():
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"status": "ok"}


def test_pin_override_is_mirrored_to_server(admin_client, agent):
    now = datetime.now(timezone.utc)
    ev = _event("pin.override_granted", starts_at=now.isoformat(), ends_at=(now + timedelta(minutes=60)).isoformat())
    agent.post("/api/v1/agent/events", json={"events": [ev]})
    overrides = admin_client.get(f"/api/v1/devices/{agent.device_id}/overrides").json()
    assert len(overrides) == 1 and overrides[0]["reason"] == "Lock-screen PIN"
    assert len(agent.get("/api/v1/agent/state").json()["overrides"]) == 1


def test_pin_override_rejects_bogus_windows(admin_client, agent):
    now = datetime.now(timezone.utc)
    too_long = _event("pin.override_granted", starts_at=now.isoformat(), ends_at=(now + timedelta(days=30)).isoformat())
    garbage = _event("pin.override_granted", starts_at="yesterday", ends_at=None)
    agent.post("/api/v1/agent/events", json={"events": [too_long, garbage]})
    assert admin_client.get(f"/api/v1/devices/{agent.device_id}/overrides").json() == []


def test_pin_releases_immediate_lock(admin_client, agent):
    lock = admin_client.post(f"/api/v1/devices/{agent.device_id}/lock", json={}).json()
    agent.post("/api/v1/agent/events", json={"events": [_event("lock.released_by_pin", lock_request_id=lock["id"])]})
    assert admin_client.get(f"/api/v1/devices/{agent.device_id}/lock").json() is None
    assert agent.get("/api/v1/agent/state").json()["immediate_lock"] is None
