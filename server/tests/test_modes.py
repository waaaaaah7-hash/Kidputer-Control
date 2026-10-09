"""Managed and Trusted modes (roadmap #16)."""

import io
import json
import zipfile

ROOT = "KidputerControl-Installer"


def test_new_devices_are_managed_and_mode_can_change(admin_client):
    dev = admin_client.post("/api/v1/devices", json={"name": "Kid PC"}).json()["device_id"]
    assert admin_client.get(f"/api/v1/devices/{dev}").json()["mode"] == "managed"
    r = admin_client.patch(f"/api/v1/devices/{dev}", json={"mode": "trusted"})
    assert r.status_code == 200 and r.json()["mode"] == "trusted"
    assert admin_client.patch(f"/api/v1/devices/{dev}", json={"mode": "other"}).status_code == 422
    trusted = admin_client.post("/api/v1/devices", json={"name": "Laptop", "mode": "trusted"}).json()["device_id"]
    assert admin_client.get(f"/api/v1/devices/{trusted}").json()["mode"] == "trusted"


def test_installer_carries_the_mode_and_saves_it(admin_client):
    dev = admin_client.post("/api/v1/devices", json={"name": "Kid PC"}).json()["device_id"]
    r = admin_client.post(f"/api/v1/devices/{dev}/installer", json={"mode": "trusted"})
    config = json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read(f"{ROOT}/install-config.json"))
    assert config["mode"] == "trusted"
    assert admin_client.get(f"/api/v1/devices/{dev}").json()["mode"] == "trusted"
    # Without a mode, the device's current mode is used.
    r = admin_client.post(f"/api/v1/devices/{dev}/installer", json={})
    assert json.loads(zipfile.ZipFile(io.BytesIO(r.content)).read(f"{ROOT}/install-config.json"))["mode"] == "trusted"


def test_agent_state_includes_the_mode(admin_client, agent):
    assert agent.get("/api/v1/agent/state").json()["mode"] == "managed"
    admin_client.patch(f"/api/v1/devices/{agent.device_id}", json={"mode": "trusted"})
    assert agent.get("/api/v1/agent/state").json()["mode"] == "trusted"
