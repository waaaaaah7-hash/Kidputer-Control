"""One-click installer: a zip with the agent, a pre-filled install-config.json and a double-click launcher.

Built in memory per download, because each download carries a freshly issued enrollment code.
"""

import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

# Only what runs on the PC: the payload folder plus the install/uninstall scripts (no tests, no docs).
_AGENT_ENTRIES = ("Install.ps1", "Uninstall.ps1", "KidputerControl")

LAUNCHER_NAME = "Install Kidputer Control.cmd"

# Batch files need CRLF line endings. Re-launches itself elevated (one UAC prompt), then runs
# Install.ps1, which picks up install-config.json from its own folder. The path is passed through an
# environment variable so folders with quotes or apostrophes (C:\Users\O'Brien) still work.
_LAUNCHER = "\r\n".join(
    [
        "@echo off",
        "rem Kidputer Control: double-click to install on this PC.",
        "net session >nul 2>&1",
        "if %errorlevel% neq 0 (",
        '  set "KC_SELF=%~f0"',
        '  powershell -NoProfile -Command "Start-Process -FilePath $env:KC_SELF -Verb RunAs"',
        "  exit /b",
        ")",
        'cd /d "%~dp0"',
        'powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install.ps1"',
        "echo.",
        "pause",
        "",
    ]
)


def _readme(device_name: str, expires_at: datetime) -> str:
    lines = [
        f"Kidputer Control installer for: {device_name}",
        "",
        "1. Copy this folder to the child's PC (unzip it first if you downloaded the zip there).",
        f"2. Double-click \"{LAUNCHER_NAME}\" and click Yes when Windows asks for administrator permission.",
        "   Sign in as a parent / administrator account to do this, not as the child.",
        "3. Choose the child's Windows account from the list.",
        "",
        f"This installer contains a one-time enrollment code that expires {expires_at:%Y-%m-%d %H:%M} UTC.",
        "If it has expired, download a new installer from the dashboard.",
        "",
        "If Windows shows \"Windows protected your PC\", click \"More info\" and then \"Run anyway\".",
        "",
        "To remove Kidputer Control later, run Uninstall.ps1 from an administrator PowerShell.",
        "",
    ]
    return "\r\n".join(lines)


def build_installer_zip(agent_dir: Path, api_url: str, enrollment_code: str, expires_at: datetime,
                        device_name: str, child_user: str | None, mode: str = "managed") -> bytes:
    if not (agent_dir / "Install.ps1").is_file():
        raise FileNotFoundError(f"agent files not found in {agent_dir}")
    config = {"api_url": api_url, "enrollment_code": enrollment_code, "expires_at": expires_at.isoformat(), "mode": mode}
    if api_url.startswith("http://"):
        config["allow_insecure_http"] = True  # local development server; hosted deployments are https
    if child_user:
        config["child_user"] = child_user

    buf = io.BytesIO()
    root = "KidputerControl-Installer"
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for entry in _AGENT_ENTRIES:
            path = agent_dir / entry
            files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
            for f in files:
                if "__pycache__" in f.parts:
                    continue
                z.write(f, f"{root}/{f.relative_to(agent_dir).as_posix()}")
        z.writestr(f"{root}/install-config.json", json.dumps(config, indent=2))
        z.writestr(f"{root}/{LAUNCHER_NAME}", _LAUNCHER)
        z.writestr(f"{root}/README.txt", _readme(device_name, expires_at))
    return buf.getvalue()
