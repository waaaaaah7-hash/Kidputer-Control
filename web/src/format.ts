import type { ActivityEvent, Device, ScheduleEntry } from "./api";

export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] as const;

/** A device counts as offline once it misses a few polls (default poll interval is 30 s). */
export const OFFLINE_AFTER_MS = 3 * 60 * 1000;

export type Tone = "ok" | "warn" | "danger" | "muted" | "info";

export interface DeviceStatus {
  label: string;
  detail: string;
  tone: Tone;
  online: boolean;
}

export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "never";
  const diff = Math.round((new Date(iso).getTime() - now) / 1000);
  const abs = Math.abs(diff);
  const fmt = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (abs < 45) return diff <= 0 ? "just now" : "in a few seconds";
  if (abs < 3600) return fmt.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return fmt.format(Math.round(diff / 3600), "hour");
  return fmt.format(Math.round(diff / 86400), "day");
}

export function formatTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(iso));
}

export function formatDateTime(iso: string | null | undefined, timeZone?: string): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short", timeZone }).format(new Date(iso));
}

const REASONS: Record<string, string> = {
  schedule: "scheduled bedtime",
  immediate: "locked by a parent",
  override: "override",
};

export function deviceStatus(device: Device, now: number = Date.now()): DeviceStatus {
  if (!device.enrolled) return { label: "Waiting for install", detail: "Enter the enrollment code on the PC", tone: "muted", online: false };
  const lastSeen = device.last_seen_at ? new Date(device.last_seen_at).getTime() : 0;
  const online = now - lastSeen < OFFLINE_AFTER_MS;
  const s = device.last_status ?? {};
  if (!online) {
    return { label: "Offline", detail: `Last seen ${relativeTime(device.last_seen_at, now)}. It keeps enforcing its last schedule.`, tone: "muted", online };
  }
  if (s.tamper_problems && s.tamper_problems.length) {
    return { label: "Tamper alert", detail: s.tamper_problems.join("; "), tone: "danger", online };
  }
  const tz = device.timezone;
  if (s.mode === "locked") {
    const until = s.reason === "schedule" && s.unlock_at ? ` until ${formatTime(s.unlock_at, tz)}` : "";
    return { label: "Locked", detail: `${capitalize(REASONS[s.reason ?? ""] ?? "locked")}${until}`, tone: "danger", online };
  }
  if (s.mode === "countdown") return { label: "Locking soon", detail: "Countdown is showing on screen", tone: "warn", online };
  if (s.reason === "override" && s.override_ends_at) {
    return { label: "Unlocked", detail: `Override until ${formatTime(s.override_ends_at, tz)}`, tone: "info", online };
  }
  if (s.reason === "disabled") return { label: "Not enforcing", detail: "Enforcement is turned off", tone: "warn", online };
  return { label: "Unlocked", detail: s.console_user_is_child === false ? "Child isn't signed in" : "Within allowed hours", tone: "ok", online };
}

function capitalize(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** "21:30:00" -> "21:30" (what <input type="time"> expects). */
export function hhmm(t: string): string {
  return t.slice(0, 5);
}

/** Weekly schedule as one editable row per weekday. */
export interface ScheduleRow {
  enabled: boolean;
  lock_at: string;
  unlock_at: string;
}

export const DEFAULT_ROW: ScheduleRow = { enabled: false, lock_at: "21:00", unlock_at: "07:00" };

export function toRows(entries: ScheduleEntry[]): ScheduleRow[] {
  return DAYS.map((_, day) => {
    const e = entries.find((x) => x.day_of_week === day);
    return e ? { enabled: true, lock_at: hhmm(e.lock_at), unlock_at: hhmm(e.unlock_at) } : { ...DEFAULT_ROW };
  });
}

export function toEntries(rows: ScheduleRow[]): ScheduleEntry[] {
  return rows.flatMap((r, day) => (r.enabled ? [{ day_of_week: day, lock_at: r.lock_at, unlock_at: r.unlock_at }] : []));
}

/** Human description of a lock window, e.g. "21:00 → 07:00 next day (10 h)". */
export function describeWindow(lockAt: string, unlockAt: string): string {
  const [lh = 0, lm = 0] = lockAt.split(":").map(Number);
  const [uh = 0, um = 0] = unlockAt.split(":").map(Number);
  let minutes = uh * 60 + um - (lh * 60 + lm);
  const overnight = minutes <= 0;
  if (overnight) minutes += 24 * 60;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  const len = m ? `${h} h ${m} min` : `${h} h`;
  return overnight ? `until ${hhmm(unlockAt)} next day · ${len}` : `until ${hhmm(unlockAt)} · ${len}`;
}

const EVENT_LABELS: Record<string, string> = {
  "session.locked": "PC locked",
  "session.unlocked": "PC unlocked",
  "session.countdown_started": "Countdown started",
  "pin.failed": "Wrong PIN entered",
  "pin.lockout": "PIN entry locked out",
  "pin.override_granted": "Unlocked with PIN",
  "pin.rejected_locked_out": "PIN tried during lockout",
  "lock.requested": "Immediate lock requested",
  "lock.applied": "Immediate lock applied",
  "lock.cleared": "Immediate lock cleared",
  "lock.released_by_pin": "Immediate lock released with PIN",
  "override.created": "Override granted",
  "override.cancelled": "Override cancelled",
  "message.sent": "Message sent",
  "message.displayed": "Message shown",
  "message.dismissed": "Message closed by child",
  "message.cleared": "Message removed",
  "message.close_blocked": "Tried to close a persistent message",
  "tamper.detected": "Tamper detected",
  "tamper.resolved": "Tamper resolved",
  "ui_agent.restarted": "Lock screen was closed and restarted",
  "agent.started": "Agent started",
  "agent.installed": "Agent installed",
  "agent.uninstalled": "Agent uninstalled",
  "agent.unauthorized": "Device token rejected",
  "agent.child_is_admin": "Child account is an administrator",
  "device.enrolled": "PC enrolled",
  "device.created": "Device added",
  "device.updated": "Device renamed or time zone changed",
  "device.deleted": "Device deleted",
  "device.token_revoked": "New enrollment code issued",
  "config.schedule_changed": "Schedule changed",
  "config.settings_changed": "Settings changed",
  "config.pin_changed": "PIN changed",
  "config.pin_cleared": "PIN removed",
  "clock.skew": "PC clock is wrong",
  "agent.reconnected": "PC back online",
  "ui_agent.error": "Lock screen error",
  "admin.login": "Parent signed in",
  "admin.login_locked": "Sign-in locked after failed attempts",
  "admin.totp_enabled": "Two-factor sign-in turned on",
  "admin.password_changed": "Password changed",
  "admin.bootstrapped": "First admin created",
  "admin.passkey_added": "Passkey added",
  "admin.passkey_removed": "Passkey removed",
  "admin.device_remembered": "Device remembered",
  "admin.device_forgotten": "Remembered device forgotten",
  "admin.session_revoked": "Signed out a device",
  "device.installer_downloaded": "Installer downloaded",
  "app.blocked": "Blocked app",
  "app.time_up": "App's allowed time ended",
  "config.app_rules_changed": "App rules changed",
  "apps.custom_added": "App added",
  "apps.custom_removed": "App removed from the list",
  "software.installed": "App installed",
  "software.uninstalled": "App removed",
  "software.vpn_detected": "VPN or remote-control app",
  "program.first_run": "New program run",
  "archive.new": "New archive file",
  "archive.opened": "Archive opened",
  "account.created": "Windows account created",
  "account.deleted": "Windows account deleted",
  "account.admin_added": "New administrator",
  "account.admin_removed": "Administrator removed",
  "defender.disabled": "Virus protection switched off",
  "defender.enabled": "Virus protection back on",
  "usb.connected": "USB drive connected",
  "agent.updated": "Kidputer Control updated",
  "agent.update_failed": "Update failed",
  "app.stop_requested": "Asked to close an app",
  "app.stop_warning": "App closing warning shown",
  "app.stopped": "App closed",
  "device.silent": "PC went silent",
  "device.offline": "PC turned off",
  "device.back_online": "PC back online",
  "device.agent_was_stopped": "Kidputer Control was stopped",
  "pc.shutting_down": "PC shutting down",
  "pc.resumed": "PC woke from sleep",
  "admin.invite_created": "Parent invited",
  "admin.invite_cancelled": "Parent invite cancelled",
  "admin.invite_accepted": "Parent joined",
  "admin.parent_removed": "Parent removed",
};

export function eventLabel(e: Pick<ActivityEvent, "event_type">): string {
  return EVENT_LABELS[e.event_type] ?? e.event_type;
}

export function timeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (k: string) => string[] };
  const zones = intl.supportedValuesOf?.("timeZone") ?? [];
  return zones.includes("UTC") ? zones : ["UTC", ...zones];
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** "Chrome on Windows", "Safari on iPhone", ... from a browser's user-agent string. */
export function describeUserAgent(ua: string | null | undefined): string {
  if (!ua) return "Unknown device";
  const browser = /Edg(e|A|iOS)?\//.test(ua)
    ? "Edge"
    : /OPR\//.test(ua)
      ? "Opera"
      : /SamsungBrowser/.test(ua)
        ? "Samsung Internet"
        : /Firefox\/|FxiOS/.test(ua)
          ? "Firefox"
          : /Chrome\/|CriOS/.test(ua)
            ? "Chrome"
            : /Safari\//.test(ua)
              ? "Safari"
              : "Browser";
  const os = /iPhone/.test(ua)
    ? "iPhone"
    : /iPad/.test(ua)
      ? "iPad"
      : /Android/.test(ua)
        ? "Android"
        : /Windows/.test(ua)
          ? "Windows"
          : /CrOS/.test(ua)
            ? "Chromebook"
            : /Mac OS X|Macintosh/.test(ua)
              ? "Mac"
              : /Linux/.test(ua)
                ? "Linux"
                : "";
  return os ? `${browser} on ${os}` : browser;
}

const AUTH_METHODS: Record<string, string> = {
  passkey: "Passkey",
  password_totp: "Password + code",
  password_remembered: "Password, remembered device",
  password: "Password",
};

export function authMethodLabel(method: string | null | undefined): string {
  return (method && AUTH_METHODS[method]) || "Signed in";
}

