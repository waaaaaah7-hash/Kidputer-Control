"""Agent auto-update (roadmap #13): bundle fingerprints, the manifest and file downloads."""

import hashlib

import pytest

from app.agent_bundle import fingerprint, load_bundle
from app.config import get_settings


def test_fingerprint_matches_the_agent():
    # Same vector as agent/tests/Update.Tests.ps1.
    files = [("Watchdog.ps1", "ab" * 32), ("Modules\\KidputerControl.Common.psm1", "CD" * 32)]
    assert fingerprint(files) == "E7913FF9C10CBB3E6EFDCB0D902D65DF7BEF313D8B6D92C5A2F68483680F9723"


@pytest.fixture
def agent_dir(tmp_path, monkeypatch):
    """A stand-in agent folder shipped by the server, version 9.8.7."""
    root = tmp_path / "KidputerControl"
    (root / "Modules").mkdir(parents=True)
    (root / "Watchdog.ps1").write_text("watchdog")
    (root / "Modules" / "KidputerControl.Common.psm1").write_text("$script:AgentVersion = '9.8.7'\n")
    monkeypatch.setenv("KC_AGENT_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    monkeypatch.delenv("KC_AGENT_DIR")
    get_settings.cache_clear()
    load_bundle.cache_clear()


def test_state_names_the_bundle_and_manifest_lists_files(agent, agent_dir):
    want = agent.get("/api/v1/agent/state").json()["agent_update"]
    assert want["version"] == "9.8.7"
    m = agent.get("/api/v1/agent/update/manifest").json()
    assert m["bundle"] == want["bundle"] and m["version"] == "9.8.7"
    paths = {f["path"]: f for f in m["files"]}
    assert set(paths) == {"Watchdog.ps1", "Modules\\KidputerControl.Common.psm1"}
    assert paths["Watchdog.ps1"]["sha256"] == hashlib.sha256(b"watchdog").hexdigest().upper()
    assert fingerprint([(f["path"], f["sha256"]) for f in m["files"]]) == m["bundle"]


def test_files_download_only_from_the_manifest(agent, agent_dir, client):
    r = agent.get("/api/v1/agent/update/file", params={"path": "Modules\\KidputerControl.Common.psm1"})
    assert r.status_code == 200 and b"9.8.7" in r.content
    for bad in ("..\\..\\etc\\passwd", "../KidputerControl/Watchdog.ps1", "Install.ps1", "nope"):
        assert agent.get("/api/v1/agent/update/file", params={"path": bad}).status_code == 404
    assert client.get("/api/v1/agent/update/file", params={"path": "Watchdog.ps1"}).status_code == 401
    assert client.get("/api/v1/agent/update/manifest").status_code == 401


def test_turning_auto_update_off(admin_client, agent, agent_dir):
    admin_client.patch(f"/api/v1/devices/{agent.device_id}/settings", json={"auto_update": False})
    assert agent.get("/api/v1/agent/state").json()["agent_update"] is None
    assert admin_client.get(f"/api/v1/devices/{agent.device_id}/settings").json()["auto_update"] is False


def test_dashboard_shows_when_the_pc_runs_another_version(admin_client, agent, agent_dir):
    bundle = agent.get("/api/v1/agent/update/manifest").json()["bundle"]
    agent.post("/api/v1/agent/heartbeat", json={"agent_version": "1.0.0", "status": {"agent_bundle": "OLD"}})
    assert admin_client.get(f"/api/v1/devices/{agent.device_id}").json()["agent_update_available"] == "9.8.7"
    agent.post("/api/v1/agent/heartbeat", json={"agent_version": "9.8.7", "status": {"agent_bundle": bundle}})
    assert admin_client.get(f"/api/v1/devices/{agent.device_id}").json()["agent_update_available"] is None


def test_real_agent_folder_has_a_version():
    b = load_bundle(str(__import__("pathlib").Path(__file__).resolve().parents[2] / "agent"))
    assert b is not None and b.version != "unknown" and len(b.files) >= 6
