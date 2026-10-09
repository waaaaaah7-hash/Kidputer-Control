import io
import json
import zipfile

import pytest

from app.config import Settings, get_settings
from app.main import app

ROOT = "KidputerControl-Installer"


def _new_device(c):
    return c.post("/api/v1/devices", json={"name": "Kid PC"}).json()


def _zip(resp):
    return zipfile.ZipFile(io.BytesIO(resp.content))


@pytest.fixture
def settings_override():
    """Swap settings for one test, e.g. a public URL or a missing agent folder."""

    def apply(**kw):
        app.dependency_overrides[get_settings] = lambda: Settings(**kw)

    yield apply
    app.dependency_overrides.pop(get_settings, None)


def test_installer_zip_contents(admin_client):
    dev = _new_device(admin_client)
    r = admin_client.post(f"/api/v1/devices/{dev['device_id']}/installer", json={"child_user": "Sam"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "KidputerControl-Installer.zip" in r.headers["content-disposition"]
    names = set(_zip(r).namelist())
    for expected in [
        "Install.ps1",
        "Uninstall.ps1",
        "KidputerControl/Watchdog.ps1",
        "KidputerControl/UiAgent.ps1",
        "KidputerControl/Modules/KidputerControl.Common.psm1",
        "KidputerControl/Modules/KidputerControl.Native.cs",
        "install-config.json",
        "Install Kidputer Control.cmd",
        "README.txt",
    ]:
        assert f"{ROOT}/{expected}" in names, expected
    assert not any("/tests/" in n or n.endswith(".md") for n in names)

    z = _zip(r)
    config = json.loads(z.read(f"{ROOT}/install-config.json"))
    assert config["enrollment_code"] == r.headers["x-enrollment-code"]
    assert config["api_url"] == "http://testserver"
    assert config["allow_insecure_http"] is True  # only because the test server is plain http
    assert config["child_user"] == "Sam"
    launcher = z.read(f"{ROOT}/Install Kidputer Control.cmd").decode()
    assert "\r\n" in launcher and "\n" not in launcher.replace("\r\n", "")
    assert "-Verb RunAs" in launcher and "Install.ps1" in launcher


def test_installer_code_replaces_the_shown_code_and_enrolls(admin_client):
    dev = _new_device(admin_client)
    r = admin_client.post(f"/api/v1/devices/{dev['device_id']}/installer", json={})
    config = json.loads(_zip(r).read(f"{ROOT}/install-config.json"))
    assert "child_user" not in config
    # The code shown at creation is superseded by the one inside the installer.
    assert admin_client.post("/api/v1/agent/enroll", json={"enrollment_code": dev["enrollment_code"]}).status_code == 401
    assert admin_client.post("/api/v1/agent/enroll", json={"enrollment_code": config["enrollment_code"]}).status_code == 200
    events = admin_client.get("/api/v1/events", params={"event_type": "device.installer_downloaded"}).json()
    assert len(events) == 1


def test_installed_pc_needs_explicit_replace(admin_client, agent):
    url = f"/api/v1/devices/{agent.device_id}/installer"
    r = admin_client.post(url, json={})
    assert r.status_code == 409
    assert agent.get("/api/v1/agent/state").status_code == 200  # still connected
    assert admin_client.post(url, json={"replace": True}).status_code == 200
    assert agent.get("/api/v1/agent/state").status_code == 401  # disconnected until reinstalled


def test_public_url_setting_wins(admin_client, settings_override):
    settings_override(public_url="https://kc.example.com/")
    dev = _new_device(admin_client)
    r = admin_client.post(f"/api/v1/devices/{dev['device_id']}/installer", json={})
    config = json.loads(_zip(r).read(f"{ROOT}/install-config.json"))
    assert config["api_url"] == "https://kc.example.com"
    assert "allow_insecure_http" not in config


def test_missing_agent_files_change_nothing(admin_client, settings_override, tmp_path):
    dev = _new_device(admin_client)
    settings_override(agent_dir=str(tmp_path))
    r = admin_client.post(f"/api/v1/devices/{dev['device_id']}/installer", json={})
    assert r.status_code == 503
    assert admin_client.post("/api/v1/agent/enroll", json={"enrollment_code": dev["enrollment_code"]}).status_code == 200


def test_installer_validation_and_auth(admin_client, client):
    dev = _new_device(admin_client)
    url = f"/api/v1/devices/{dev['device_id']}/installer"
    assert admin_client.post(url, json={"child_user": "bad/name"}).status_code == 422
    admin_client.post("/api/v1/auth/logout")
    assert admin_client.post(url, json={}).status_code == 401
