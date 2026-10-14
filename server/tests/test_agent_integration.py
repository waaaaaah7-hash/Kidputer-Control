"""Runs the PowerShell agent module's protocol code against a live API server.

Skipped when PowerShell (pwsh) isn't installed. Set KC_PWSH to use a specific binary.
"""

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from app.main import app

from .conftest import CSRF, PASSWORD, make_admin, totp_code

PWSH = os.environ.get("KC_PWSH") or shutil.which("pwsh")
SCRIPT = Path(__file__).resolve().parents[2] / "agent" / "tests" / "Integration.Tests.ps1"

pytestmark = pytest.mark.skipif(PWSH is None, reason="pwsh not installed")


@pytest.fixture
def live_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started:
        assert time.time() < deadline, "server did not start"
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


def test_agent_module_against_live_api(live_server):
    _, secret = make_admin()
    with httpx.Client(base_url=live_server, headers=CSRF) as admin:
        admin.post("/api/v1/auth/login", json={"email": "parent@example.com", "password": PASSWORD})
        assert admin.post("/api/v1/auth/login/mfa", json={"code": totp_code(secret)}).status_code == 200
        created = admin.post("/api/v1/devices", json={"name": "Integration PC", "timezone": "Europe/London"}).json()
        dev = created["device_id"]
        admin.put(f"/api/v1/devices/{dev}/schedule", json={"entries": [{"day_of_week": d, "lock_at": "21:00", "unlock_at": "07:00"} for d in range(7)]})
        admin.put(f"/api/v1/devices/{dev}/pin", json={"pin": "246810"})
        admin.post(f"/api/v1/devices/{dev}/lock", json={"reason": "integration"})
        stop = admin.post(f"/api/v1/devices/{dev}/apps/stop", json={"path": "C:\\Games\\javaw.exe", "name": "Minecraft"}).json()

        env = {**os.environ, "KC_IT_API_URL": live_server, "KC_IT_ENROLL_CODE": created["enrollment_code"], "KC_IT_PIN": "246810"}
        proc = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, proc.stdout + proc.stderr

        # The server saw the agent's events exactly once and applied their side effects.
        events = admin.get("/api/v1/events", params={"device_id": dev, "source": "agent"}).json()
        types = sorted(e["event_type"] for e in events)
        assert types == ["lock.released_by_pin", "pin.override_granted", "tamper.detected"]
        assert admin.get(f"/api/v1/devices/{dev}/lock").json() is None
        overrides = admin.get(f"/api/v1/devices/{dev}/overrides").json()
        assert [o["reason"] for o in overrides] == ["Lock-screen PIN"]
        assert admin.get(f"/api/v1/devices/{dev}/commands/{stop['id']}").json()["status"] == "done"
        procs = admin.get(f"/api/v1/devices/{dev}/processes").json()["processes"]
        assert [(p["pid"], p["company"]) for p in procs] == [(4242, "Mojang")]
        scan = admin.get(f"/api/v1/devices/{dev}/scans/C").json()["scan"]
        assert scan["tree"]["s"] == 4096 and scan["tree"]["c"][0]["n"] == "Games"
        device = admin.get(f"/api/v1/devices/{dev}").json()
        assert device["enrolled"] and device["last_status"]["ui_agent_alive"] is True
