"""Parent notifications (roadmap #10): email and phone push, per-category levels, quiet hours, digests.

Flow: something worth telling parents happens (an agent event, or a server check such as "PC went
silent") -> `queue_for_event` adds one Notification row per parent, at that parent's level for the
category -> the background job (`dispatch`) sends immediate ones, grouping repeats, and once a day
sends the digest.
"""

import base64
import json
import logging
import smtplib
import ssl
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

import requests
from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import AdminUser, AppSecret, Device, Notification, NotificationSettings, PushSubscription

log = logging.getLogger(__name__)

LEVELS = ("immediate", "digest", "off")


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    description: str
    default: str
    plural: str  # "3 {plural}" in a grouped alert
    # False until the feature that produces these events ships (they're hidden from the settings page).
    ready: bool = True


CATEGORIES: tuple[Category, ...] = (
    Category("silent", "PC went silent", "The PC stopped checking in: it may be off or offline, or Kidputer Control was stopped.", "immediate", "silence alerts"),
    Category("tamper", "Tamper alerts", "Kidputer Control's files changed, it was uninstalled or stopped, or the child's account became an administrator.", "immediate", "tamper alerts"),
    Category("time_requests", "Requests for more time", "Your child asked for more time from the lock screen.", "immediate", "time requests", ready=False),
    Category("install_requests", "App install requests", "Your child asked to install an app.", "immediate", "install requests", ready=False),
    Category("installs", "Apps installed or removed", "Programs installed or uninstalled on the PC.", "immediate", "app changes", ready=False),
    Category("first_run", "New programs run", "A program ran for the first time, for example a game from a zip file.", "immediate", "new programs", ready=False),
    Category("vpn", "VPN and remote-access apps", "A VPN or remote-control app (NordVPN, AnyDesk, TeamViewer, ...) appeared.", "immediate", "VPN or remote-access apps", ready=False),
    Category("accounts", "Windows accounts", "A Windows account was created or deleted, or someone became an administrator.", "immediate", "account changes", ready=False),
    Category("defender", "Windows Defender", "Virus protection was switched off or back on.", "immediate", "Defender changes", ready=False),
    Category("blocked_apps", "Blocked apps", "Your child tried to open an app outside its allowed times.", "digest", "blocked apps", ready=False),
    Category("limits", "Daily limits", "A daily screen-time or app limit ran out.", "digest", "limits reached", ready=False),
    Category("archives", "Zip and archive files", "New zip, rar or 7z files, or archives being opened.", "digest", "archive files", ready=False),
    Category("usb", "USB drives", "A USB stick or external drive was connected.", "digest", "USB drives", ready=False),
    Category("pin", "Wrong lock-screen PINs", "PIN entry locked after too many wrong attempts.", "digest", "PIN lockouts"),
    Category("power", "PC turned off or back online", "The PC was shut down, or came back after being offline.", "off", "power changes"),
)
CATEGORY_BY_ID = {c.id: c for c in CATEGORIES}

# Event type -> (category, short title). Event types not listed here never notify.
EVENT_NOTIFICATIONS: dict[str, tuple[str, str]] = {
    "device.silent": ("silent", "No contact"),
    "device.agent_was_stopped": ("tamper", "Kidputer Control was stopped"),
    "tamper.detected": ("tamper", "Tamper alert"),
    "agent.uninstalled": ("tamper", "Kidputer Control uninstalled"),
    "agent.unauthorized": ("tamper", "PC was disconnected"),
    "agent.child_is_admin": ("tamper", "Child's account is an administrator"),
    "clock.skew": ("tamper", "PC clock is wrong"),
    "time_request.created": ("time_requests", "Asks for more time"),
    "install_request.created": ("install_requests", "Asks to install an app"),
    "software.installed": ("installs", "App installed"),
    "software.uninstalled": ("installs", "App removed"),
    "program.first_run": ("first_run", "New program run"),
    "software.vpn_detected": ("vpn", "VPN or remote-access app"),
    "account.created": ("accounts", "New Windows account"),
    "account.deleted": ("accounts", "Windows account deleted"),
    "account.admin_added": ("accounts", "New administrator"),
    "account.admin_removed": ("accounts", "Administrator removed"),
    "defender.disabled": ("defender", "Virus protection off"),
    "defender.enabled": ("defender", "Virus protection back on"),
    "app.blocked": ("blocked_apps", "Blocked app"),
    "limit.reached": ("limits", "Daily limit reached"),
    "archive.new": ("archives", "New archive file"),
    "archive.opened": ("archives", "Archive opened"),
    "usb.connected": ("usb", "USB drive connected"),
    "pin.lockout": ("pin", "PIN locked"),
    "device.offline": ("power", "Turned off"),
    "device.back_online": ("power", "Back online"),
}

# Gather a burst (50 zip files extracted at once) into one alert: wait this long after the first item...
GROUP_WAIT = timedelta(seconds=20)
# ...or, if this group already alerted recently, collect for longer before the next one.
REPEAT_WINDOW = timedelta(minutes=10)
REPEAT_WAIT = timedelta(minutes=5)


# --- Settings ----------------------------------------------------------------------------------------


def settings_for(db: Session, user: AdminUser) -> NotificationSettings:
    ns = db.get(NotificationSettings, user.id)
    if ns is None:
        ns = NotificationSettings(user_id=user.id, levels={}, email_enabled=True, push_enabled=True, timezone="UTC", digest_hour=18)
        db.add(ns)
        db.flush()
    return ns


def level_for(ns: NotificationSettings, category: str) -> str:
    level = (ns.levels or {}).get(category)
    return level if level in LEVELS else CATEGORY_BY_ID[category].default


def in_quiet_hours(ns: NotificationSettings, now: datetime) -> bool:
    if ns.quiet_start is None or ns.quiet_end is None or ns.quiet_start == ns.quiet_end:
        return False
    t = now.astimezone(ZoneInfo(ns.timezone)).time()
    if ns.quiet_start < ns.quiet_end:
        return ns.quiet_start <= t < ns.quiet_end
    return t >= ns.quiet_start or t < ns.quiet_end  # overnight, e.g. 22:00-07:00


# --- Queueing -----------------------------------------------------------------------------------------


def device_path(device_id: Any, tab: str | None = None) -> str:
    return f"/devices/{device_id}" + (f"?tab={tab}" if tab else "")


def queue_for_event(
    db: Session,
    *,
    event_type: str,
    message: str | None,
    device: Device | None,
    event_id: int | None = None,
    url: str | None = None,
) -> int:
    """Queue a notification for every parent who wants this kind of event. Returns how many were queued."""
    entry = EVENT_NOTIFICATIONS.get(event_type)
    if entry is None:
        return 0
    category, short = entry
    title = f"{device.name}: {short}" if device is not None else short
    body = message or short
    if url is None and device is not None:
        url = device_path(device.id)
    queued = 0
    for user in db.scalars(select(AdminUser).where(AdminUser.disabled_at.is_(None))):
        level = level_for(settings_for(db, user), category)
        if level == "off":
            continue
        db.add(
            Notification(
                user_id=user.id,
                device_id=device.id if device is not None else None,
                event_id=event_id,
                category=category,
                level=level,
                title=title[:200],
                body=body,
                url=url,
            )
        )
        queued += 1
    return queued


# --- Channels ------------------------------------------------------------------------------------------


def send_email(settings: Settings, to: str, subject: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if settings.smtp_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    with server:
        if settings.smtp_security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(msg)


def vapid_key(db: Session) -> Vapid02:
    """The server's Web Push signing key, created on first use and kept in the database."""
    row = db.get(AppSecret, "vapid_private_pem")
    if row is None:
        v = Vapid02()
        v.generate_keys()
        pem = v.private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ).decode()
        db.add(AppSecret(name="vapid_private_pem", value=pem))
        db.flush()
        return v
    return Vapid02.from_pem(row.value.encode())


def vapid_public_key(db: Session) -> str:
    """applicationServerKey for the browser: the raw public point, base64url without padding."""
    raw = vapid_key(db).public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# Swappable in tests, which can't reach real push services.
push_session: requests.Session | None = None


def send_push(db: Session, sub: PushSubscription, payload: dict[str, Any], contact: str) -> bool:
    """Send one push message. Returns False when the subscription is gone (and deletes it)."""
    try:
        webpush(
            subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=json.dumps(payload),
            vapid_private_key=vapid_key(db),
            vapid_claims={"sub": f"mailto:{contact}"},
            ttl=24 * 3600,
            timeout=10,
            requests_session=push_session,
        )
    except WebPushException as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in (404, 410):  # the browser unsubscribed or the app was removed
            db.delete(sub)
            return False
        log.warning("push to %s failed: %s", sub.endpoint[:60], exc)
        return True
    except requests.RequestException as exc:
        log.warning("push to %s failed: %s", sub.endpoint[:60], exc)
        return True
    sub.last_success_at = datetime.now().astimezone()
    return True


def deliver(db: Session, settings: Settings, user: AdminUser, ns: NotificationSettings, title: str, body: str, url: str | None) -> dict[str, Any]:
    """Send one message to one parent on every channel they have on. Returns what was attempted."""
    result: dict[str, Any] = {"email": False, "push": 0}
    link = f"{settings.dashboard_url}{url}" if url and settings.dashboard_url else None
    if ns.email_enabled and settings.email_configured:
        text = body + (f"\n\nOpen the dashboard: {link}" if link else "") + "\n\nChange which alerts you get under Account > Notifications."
        try:
            send_email(settings, user.email, title, text)
            result["email"] = True
        except (OSError, smtplib.SMTPException) as exc:
            log.warning("email to %s failed: %s", user.email, exc)
            result["email_error"] = str(exc)
    if ns.push_enabled:
        for sub in list(db.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id))):
            if send_push(db, sub, {"title": title, "body": body[:500], "url": url or "/"}, user.email):
                result["push"] += 1
    return result


# --- Sending -----------------------------------------------------------------------------------------------


def _group_text(items: list[Notification]) -> tuple[str, str]:
    if len(items) == 1:
        return items[0].title, items[0].body
    first = items[0]
    cat = CATEGORY_BY_ID[first.category]
    prefix = first.title.split(": ", 1)[0] + ": " if ": " in first.title else ""
    lines = [f"- {n.body}" for n in items[:5]]
    if len(items) > 5:
        lines.append(f"...and {len(items) - 5} more")
    return f"{prefix}{len(items)} {cat.plural}", "\n".join(lines)


def dispatch(db: Session, settings: Settings, now: datetime) -> int:
    """Send due immediate notifications, grouped per parent, PC and category. Returns messages sent."""
    pending = list(
        db.scalars(
            select(Notification).where(Notification.level == "immediate", Notification.sent_at.is_(None)).order_by(Notification.created_at)
        )
    )
    groups: dict[tuple, list[Notification]] = defaultdict(list)
    for n in pending:
        groups[(n.user_id, n.device_id, n.category)].append(n)
    sent = 0
    for (user_id, device_id, category), items in groups.items():
        user = db.get(AdminUser, user_id)
        if user is None or user.disabled_at is not None:
            for n in items:
                n.sent_at = now
            continue
        ns = settings_for(db, user)
        if in_quiet_hours(ns, now):
            continue
        last_sent = db.scalar(
            select(func.max(Notification.sent_at)).where(
                Notification.user_id == user_id,
                Notification.device_id.is_(None) if device_id is None else Notification.device_id == device_id,
                Notification.category == category,
                Notification.level == "immediate",
            )
        )
        wait = REPEAT_WAIT if last_sent is not None and now - last_sent < REPEAT_WINDOW else GROUP_WAIT
        if now - items[0].created_at < wait:
            continue
        title, body = _group_text(items)
        deliver(db, settings, user, ns, title, body, items[0].url)
        for n in items:
            n.sent_at = now
        sent += 1
    return sent


def send_digests(db: Session, settings: Settings, now: datetime) -> int:
    """Once a day, at each parent's chosen hour, send everything set to "daily digest"."""
    sent = 0
    for user in db.scalars(select(AdminUser).where(AdminUser.disabled_at.is_(None))):
        ns = settings_for(db, user)
        local = now.astimezone(ZoneInfo(ns.timezone))
        if local.hour < ns.digest_hour or ns.last_digest_on == local.date():
            continue
        items = list(
            db.scalars(
                select(Notification)
                .where(Notification.user_id == user.id, Notification.level == "digest", Notification.sent_at.is_(None))
                .order_by(Notification.created_at)
            )
        )
        ns.last_digest_on = local.date()
        if not items:
            continue
        by_cat: dict[str, list[Notification]] = defaultdict(list)
        for n in items:
            by_cat[n.category].append(n)
        parts = []
        for cat in CATEGORIES:
            if cat.id not in by_cat:
                continue
            parts.append(f"{cat.label} ({len(by_cat[cat.id])})")
            for n in by_cat[cat.id][:20]:
                when = n.created_at.astimezone(ZoneInfo(ns.timezone)).strftime("%a %H:%M")
                parts.append(f"  {when}  {n.title}: {n.body}")
            if len(by_cat[cat.id]) > 20:
                parts.append(f"  ...and {len(by_cat[cat.id]) - 20} more")
            parts.append("")
        summary = ", ".join(f"{len(v)} {CATEGORY_BY_ID[k].plural}" for k, v in by_cat.items())
        deliver(db, settings, user, ns, f"Kidputer Control daily summary: {summary}", "\n".join(parts).rstrip(), "/activity")
        for n in items:
            n.sent_at = now
        sent += 1
    return sent


def prune(db: Session, now: datetime) -> None:
    """Notifications are a delivery queue, not the history (that's the activity log): keep 30 days."""
    db.execute(delete(Notification).where(Notification.created_at < now - timedelta(days=30)))


def quiet_time(value: str | None) -> time | None:
    return None if not value else time.fromisoformat(value)
