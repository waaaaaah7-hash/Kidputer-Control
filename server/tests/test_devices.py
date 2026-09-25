from datetime import datetime, timedelta, timezone


def _new_device(c, name="Kid PC"):
    r = c.post("/api/v1/devices", json={"name": name})
    assert r.status_code == 201
    return r.json()["device_id"]


def test_create_device_returns_enrollment_code(admin_client):
    r = admin_client.post("/api/v1/devices", json={"name": "Kid PC", "timezone": "America/New_York"})
    body = r.json()
    assert len(body["enrollment_code"]) == 14  # XXXX-XXXX-XXXX
    d = admin_client.get(f"/api/v1/devices/{body['device_id']}").json()
    assert d["enrolled"] is False and d["timezone"] == "America/New_York"


def test_rejects_unknown_timezone(admin_client):
    assert admin_client.post("/api/v1/devices", json={"name": "x", "timezone": "Mars/Base"}).status_code == 422


def test_schedule_roundtrip_and_validation(admin_client):
    dev = _new_device(admin_client)
    sched = {"entries": [{"day_of_week": 0, "lock_at": "21:30", "unlock_at": "07:00"}, {"day_of_week": 5, "lock_at": "23:00", "unlock_at": "08:00"}]}
    assert admin_client.put(f"/api/v1/devices/{dev}/schedule", json=sched).status_code == 200
    got = admin_client.get(f"/api/v1/devices/{dev}/schedule").json()
    assert [e["day_of_week"] for e in got["entries"]] == [0, 5]
    assert got["entries"][0]["lock_at"] == "21:30:00"

    dup = {"entries": [{"day_of_week": 1, "lock_at": "21:00", "unlock_at": "07:00"}] * 2}
    assert admin_client.put(f"/api/v1/devices/{dev}/schedule", json=dup).status_code == 422
    empty_window = {"entries": [{"day_of_week": 1, "lock_at": "21:00", "unlock_at": "21:00"}]}
    assert admin_client.put(f"/api/v1/devices/{dev}/schedule", json=empty_window).status_code == 422


def test_settings_and_pin(admin_client):
    dev = _new_device(admin_client)
    s = admin_client.get(f"/api/v1/devices/{dev}/settings").json()
    assert s["pin_set"] is False and s["countdown_seconds"] == 300
    r = admin_client.patch(f"/api/v1/devices/{dev}/settings", json={"countdown_seconds": 120})
    assert r.json()["countdown_seconds"] == 120
    assert admin_client.put(f"/api/v1/devices/{dev}/pin", json={"pin": "12"}).status_code == 422
    assert admin_client.put(f"/api/v1/devices/{dev}/pin", json={"pin": "123456"}).status_code == 204
    assert admin_client.get(f"/api/v1/devices/{dev}/settings").json()["pin_set"] is True


def test_overrides(admin_client):
    dev = _new_device(admin_client)
    r = admin_client.post(f"/api/v1/devices/{dev}/overrides", json={"duration_minutes": 60, "reason": "homework"})
    assert r.status_code == 201
    oid = r.json()["id"]
    assert len(admin_client.get(f"/api/v1/devices/{dev}/overrides").json()) == 1

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    r = admin_client.post(f"/api/v1/devices/{dev}/overrides", json={"starts_at": (past - timedelta(hours=1)).isoformat(), "ends_at": past.isoformat()})
    assert r.status_code == 422
    assert admin_client.post(f"/api/v1/devices/{dev}/overrides", json={}).status_code == 422

    assert admin_client.delete(f"/api/v1/devices/{dev}/overrides/{oid}").status_code == 204
    assert admin_client.get(f"/api/v1/devices/{dev}/overrides").json() == []
    assert len(admin_client.get(f"/api/v1/devices/{dev}/overrides?include_past=true").json()) == 1


def test_new_message_replaces_current(admin_client):
    dev = _new_device(admin_client)
    first = admin_client.post(f"/api/v1/devices/{dev}/message", json={"body": "Dinner!", "mode": "closable"}).json()
    second = admin_client.post(f"/api/v1/devices/{dev}/message", json={"body": "Bed time", "mode": "persistent"}).json()
    assert admin_client.get(f"/api/v1/devices/{dev}/message").json()["id"] == second["id"] != first["id"]
    assert admin_client.delete(f"/api/v1/devices/{dev}/message").status_code == 204
    assert admin_client.get(f"/api/v1/devices/{dev}/message").json() is None


def test_immediate_lock_is_idempotent(admin_client):
    dev = _new_device(admin_client)
    a = admin_client.post(f"/api/v1/devices/{dev}/lock", json={}).json()
    b = admin_client.post(f"/api/v1/devices/{dev}/lock", json={"reason": "again"}).json()
    assert a["id"] == b["id"]
    assert admin_client.delete(f"/api/v1/devices/{dev}/lock").status_code == 204
    assert admin_client.get(f"/api/v1/devices/{dev}/lock").json() is None


def test_admin_actions_are_audited(admin_client):
    dev = _new_device(admin_client)
    admin_client.post(f"/api/v1/devices/{dev}/lock", json={})
    types = [e["event_type"] for e in admin_client.get(f"/api/v1/events?device_id={dev}").json()]
    assert types == ["lock.requested", "device.created"]
    assert all(e["source"] == "admin" for e in admin_client.get("/api/v1/events?source=admin").json())


def test_delete_device(admin_client):
    dev = _new_device(admin_client)
    assert admin_client.delete(f"/api/v1/devices/{dev}").status_code == 204
    assert admin_client.get(f"/api/v1/devices/{dev}").status_code == 404
    assert admin_client.get("/api/v1/events?event_type=device.deleted").json()[0]["data"]["device_id"] == dev
