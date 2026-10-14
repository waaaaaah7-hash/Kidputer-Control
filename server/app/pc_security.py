"""Security tab (roadmap #23): the one-line summary of a PC's virus protection and Windows updates.

(Not to be confused with security.py, which hashes passwords and tokens.)

The PC sends a "security" report (see agent/KidputerControl/Watchdog.ps1, Get-SecurityReport):
    defender: available, antivirus_enabled, realtime, tamper_protected, signatures_updated,
              quick_scan_at, full_scan_at, threats [{id, name, severity, status, needs_action,
              detected_at, files}], other_antivirus [{name, enabled, up_to_date}]
    updates:  windows {name, version, build}, reboot_required, reboot_required_since, paused_until,
              service_disabled, last_search_at, last_installed_at, pending [{title, kb, kind, size,
              severity, downloaded}], last_check_at, last_install {at, installed, failed, message}
    running:  {action, started_at} or null, and queued: [action]
"""

from datetime import datetime, timedelta
from typing import Any

ACTIONS = ("quick_scan", "full_scan", "update_signatures", "remove_threats", "check_updates", "install_updates")
ACTION_LABELS = {
    "quick_scan": "a quick virus scan",
    "full_scan": "a full virus scan",
    "update_signatures": "a virus definitions update",
    "remove_threats": "removing threats",
    "check_updates": "a check for Windows updates",
    "install_updates": "installing Windows updates",
}
# The kinds of Windows update the PC installs by itself (feature upgrades only if the parent ticks them).
ROUTINE_UPDATES = ("security", "quality", "definition")


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def summary(data: dict[str, Any] | None, updated_at: datetime | None, now: datetime) -> dict[str, Any] | None:
    """{level: "ok" | "attention" | "unknown", text, items: [{level, text}]} or None before the first report.

    "attention" items need a parent; "info" items are worth knowing but fine.
    """
    if not data or updated_at is None:
        return None
    if now - updated_at > timedelta(days=3):
        return {"level": "unknown", "text": f"Security not reported for {(now - updated_at).days} days", "items": []}
    items: list[dict[str, str]] = []

    def add(level: str, text: str) -> None:
        items.append({"level": level, "text": text})

    d = data.get("defender") or {}
    threats = [t for t in d.get("threats") or [] if isinstance(t, dict)]
    active = [t for t in threats if t.get("needs_action")]
    if active:
        more = f" and {len(active) - 1} more" if len(active) > 1 else ""
        add("attention", f"Threat needs action: {active[0].get('name') or 'unknown threat'}{more}")
    other_on = [a for a in d.get("other_antivirus") or [] if isinstance(a, dict) and a.get("enabled")]
    if d.get("available") and d.get("antivirus_enabled"):
        if not d.get("realtime"):
            add("attention", "Virus protection is switched off")
        sigs = _date(d.get("signatures_updated"))
        if sigs is not None and now - sigs > timedelta(days=3):
            add("attention", f"Virus definitions are {(now - sigs).days} days old")
    elif other_on:
        for a in other_on:
            if not a.get("up_to_date"):
                add("attention", f"{a.get('name')} is out of date")
    else:
        add("attention", "No virus protection found")
    week_ago = now - timedelta(days=7)
    recent = [t for t in threats if not t.get("needs_action") and (_date(t.get("detected_at")) or week_ago) > week_ago]
    if recent:
        add("info", f"{recent[0].get('name') or 'A threat'} was {recent[0].get('status') or 'dealt with'}")

    u = data.get("updates") or {}
    if u.get("service_disabled"):
        add("attention", "Windows Update is switched off")
    if u.get("paused_until"):
        add("attention", "Windows updates are paused")
    if u.get("reboot_required"):
        since = _date(u.get("reboot_required_since"))
        if since is not None and now - since > timedelta(days=2):
            add("attention", f"A restart has been needed for {(now - since).days} days to finish updates")
        else:
            add("info", "A restart will finish installing updates")
    last = u.get("last_install") if isinstance(u.get("last_install"), dict) else {}
    failed_at = _date(last.get("at"))
    if last.get("failed") and failed_at is not None and now - failed_at < timedelta(days=3):
        add("attention", "Windows updates failed to install")
    waiting = [p for p in u.get("pending") or [] if isinstance(p, dict) and p.get("kind") in ROUTINE_UPDATES]
    if waiting:
        installed = _date(u.get("last_installed_at"))
        n = len(waiting)
        stuck = installed is None or now - installed > timedelta(days=14)
        add("attention" if stuck else "info", f"{n} Windows update{'s' if n != 1 else ''} waiting")

    attention = [i for i in items if i["level"] == "attention"]
    if attention:
        more = f" (+{len(attention) - 1} more)" if len(attention) > 1 else ""
        return {"level": "attention", "text": attention[0]["text"] + more, "items": items}
    return {"level": "ok", "text": "Protected and up to date", "items": items}
