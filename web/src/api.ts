// Typed client for the Kidputer Control API. Types mirror server/app/schemas.py.
// The dashboard is served from the same origin as the API, so the session cookie is sent automatically.

import type { AppRule, AppRuleIn } from "./rules";

export interface Admin {
  id: string;
  email: string;
  /** What the parent chose to be called; `name` falls back to the start of the email. */
  display_name: string | null;
  name: string;
  totp_enabled: boolean;
}

export interface Parent {
  id: string;
  email: string;
  name: string;
  totp_enabled: boolean;
  passkeys: number;
  created_at: string;
  last_active_at: string | null;
  you: boolean;
}

export interface Invite {
  id: string;
  email: string;
  invited_by: string | null;
  created_at: string;
  expires_at: string;
}

export type NotificationLevel = "immediate" | "digest" | "off";

export interface NotificationCategory {
  id: string;
  label: string;
  description: string;
  default: NotificationLevel;
}

export interface NotificationSettings {
  email_enabled: boolean;
  push_enabled: boolean;
  levels: Record<string, NotificationLevel>;
  quiet_start: string | null;
  quiet_end: string | null;
  timezone: string;
  digest_hour: number;
  categories: NotificationCategory[];
  email_available: boolean;
  email_address: string;
  push_devices: number;
  vapid_public_key: string;
}

export type NotificationSettingsIn = Pick<
  NotificationSettings,
  "email_enabled" | "push_enabled" | "levels" | "quiet_start" | "quiet_end" | "timezone" | "digest_hour"
>;

export interface SentNotification {
  id: number;
  category: string;
  level: "immediate" | "digest";
  title: string;
  body: string;
  url: string | null;
  created_at: string;
  sent_at: string | null;
}

export interface ProcessInfo {
  pid: number;
  name: string;
  path: string | null;
  started_at: string | null;
  memory_mb: number;
  has_window: boolean;
  title: string | null;
  description: string | null;
  product: string | null;
  company: string | null;
  system: boolean;
}

export interface ProcessesReport {
  updated_at: string | null;
  processes: ProcessInfo[];
  window_titles: boolean;
}

export interface DeviceCommand {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  status: "pending" | "done" | "failed" | "expired";
  result: Record<string, unknown> | null;
  created_at: string;
  completed_at: string | null;
}

export interface CatalogApp {
  id: string;
  name: string;
  category: string;
  custom: boolean;
  matchers: Record<string, string>[];
}

export interface AppCatalog {
  categories: Record<string, string>;
  apps: CatalogApp[];
}

export interface UsageDay {
  day: string;
  total_seconds: number;
  apps: { key: string; name: string; category: string | null; seconds: number }[];
}

export interface SeenApp {
  key: string;
  name: string;
  path: string | null;
  exe: string | null;
  product: string | null;
  company: string | null;
  catalog_id: string | null;
  last_seen: string | null;
}

export interface InviteInfo {
  email: string;
  invited_by: string | null;
  expires_at: string;
}

export interface LoginResult {
  mfa_required: boolean;
  totp_setup_required: boolean;
}

/** Live status the watchdog reports in its heartbeat. All fields optional: older agents may omit some. */
export interface AgentStatus {
  mode?: "none" | "countdown" | "locked";
  reason?: string | null;
  unlock_at?: string;
  override_ends_at?: string;
  console_user_is_child?: boolean;
  ui_agent_alive?: boolean;
  tamper_problems?: string[];
  queued_events?: number;
  clock_skew_seconds?: number | null;
  pin_locked_until?: string | null;
  child_is_admin?: boolean;
}

/** Managed: the child has a standard account. Trusted: the child is an administrator. */
export type DeviceMode = "managed" | "trusted";

export interface SignedInSession {
  id: string;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
  ip: string | null;
  user_agent: string | null;
  auth_method: "passkey" | "password_totp" | "password_remembered" | "password" | null;
  remembered: boolean;
  current: boolean;
}

export interface RememberedDevice {
  id: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_used_at: string | null;
  expires_at: string;
  current: boolean;
}

export interface Passkey {
  id: string;
  name: string;
  /** Syncs between devices (iCloud Keychain, Google Password Manager, ...). */
  backed_up: boolean;
  created_at: string;
  last_used_at: string | null;
}

/** Options from the server, passed straight to the browser's passkey prompt. */
export interface PasskeyOptions<T = Record<string, unknown>> {
  challenge_id: string;
  options: T;
}

export interface Device {
  id: string;
  name: string;
  timezone: string;
  enrolled: boolean;
  hostname: string | null;
  agent_version: string | null;
  last_seen_at: string | null;
  last_status: AgentStatus | null;
  created_at: string;
  mode: DeviceMode;
  /** The agent version the server ships, when the PC still runs another one. */
  agent_update_available: string | null;
}

export interface EnrollmentCode {
  device_id: string;
  enrollment_code: string;
  expires_at: string;
}

export interface ScheduleEntry {
  day_of_week: number; // 0 = Monday
  lock_at: string; // "HH:MM" or "HH:MM:SS"
  unlock_at: string;
}

export interface DeviceSettings {
  enforcement_enabled: boolean;
  countdown_seconds: number;
  poll_interval_seconds: number;
  pin_set: boolean;
  pin_max_attempts: number;
  pin_lockout_minutes: number;
  pin_override_minutes: number;
  silent_alert_minutes: number;
  show_window_titles: boolean;
  auto_update: boolean;
  updated_at: string;
}

export type SettingsPatch = Partial<Omit<DeviceSettings, "pin_set" | "updated_at">>;

export interface Override {
  id: string;
  starts_at: string;
  ends_at: string;
  reason: string | null;
  created_at: string;
  cancelled_at: string | null;
}

export interface Message {
  id: string;
  body: string;
  mode: "persistent" | "closable";
  created_at: string;
  displayed_at: string | null;
  dismissed_at: string | null;
  cleared_at: string | null;
}

export interface LockRequest {
  id: string;
  reason: string | null;
  created_at: string;
  applied_at: string | null;
  cleared_at: string | null;
}

export type Severity = "info" | "warning" | "critical";

export interface ActivityEvent {
  id: number;
  device_id: string | null;
  source: "agent" | "admin" | "system";
  event_type: string;
  severity: Severity;
  message: string | null;
  data: Record<string, unknown> | null;
  occurred_at: string;
  received_at: string;
  actor_user_id: string | null;
  /** The parent who did it, for parent actions. */
  actor_name: string | null;
}

export interface EventQuery {
  device_id?: string;
  severity?: Severity;
  source?: ActivityEvent["source"];
  event_type?: string;
  before_id?: number;
  limit?: number;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    /** Machine-readable reason, e.g. "reauth_required" (see server/app/deps.py). */
    public readonly code?: string,
  ) {
    super(message);
  }
}

export const REAUTH_REQUIRED = "reauth_required";

export const UNAUTHORIZED_EVENT = "kc:unauthorized";

// Required by the API on state-changing requests (CSRF defence; see server/app/deps.py).
const CSRF_HEADER = "X-Kidputer-Control";

function detailMessage(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
    // FastAPI validation errors: [{loc, msg}, ...]
    if (Array.isArray(detail)) {
      return detail
        .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg).replace(/^Value error, /, "") : ""))
        .filter(Boolean)
        .join("; ");
    }
  }
  return `Request failed (HTTP ${status})`;
}

function detailCode(body: unknown): string | undefined {
  const detail = body && typeof body === "object" && "detail" in body ? (body as { detail: unknown }).detail : null;
  return detail && typeof detail === "object" && "code" in detail ? String((detail as { code: unknown }).code) : undefined;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers[CSRF_HEADER] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api/v1${path}`, {
    method,
    headers,
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const parsed: unknown = text ? JSON.parse(text) : null;
  if (!res.ok) {
    // Session expired or revoked while using the app: let the auth layer send the user to sign in.
    if (res.status === 401 && !path.startsWith("/auth/")) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    throw new ApiError(res.status, detailMessage(parsed, res.status), detailCode(parsed));
  }
  return parsed as T;
}

export interface InstallerDownload {
  blob: Blob;
  filename: string;
  code: EnrollmentCode;
}

/** POST that returns a file. The installer response also carries the newly issued enrollment code. */
async function downloadInstaller(
  deviceId: string,
  body: { child_user?: string; replace?: boolean; mode?: DeviceMode },
): Promise<InstallerDownload> {
  const res = await fetch(`/api/v1/devices/${encodeURIComponent(deviceId)}/installer`, {
    method: "POST",
    headers: { "Content-Type": "application/json", [CSRF_HEADER]: "1" },
    credentials: "same-origin",
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED_EVENT));
    const text = await res.text();
    let parsed: unknown = null;
    try {
      parsed = text ? JSON.parse(text) : null;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detailMessage(parsed, res.status), detailCode(parsed));
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? "KidputerControl-Installer.zip";
  return {
    blob: await res.blob(),
    filename,
    code: {
      device_id: deviceId,
      enrollment_code: res.headers.get("X-Enrollment-Code") ?? "",
      expires_at: res.headers.get("X-Enrollment-Expires") ?? "",
    },
  };
}

function query(params: object): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `?${s}` : "";
}

const d = (id: string) => `/devices/${encodeURIComponent(id)}`;

export const api = {
  // Auth
  login: (email: string, password: string) => request<LoginResult>("POST", "/auth/login", { email, password }),
  loginMfa: (code: string, remember = false) => request<LoginResult>("POST", "/auth/login/mfa", { code, remember }),
  reverify: (code: string) => request<void>("POST", "/auth/reverify", { code }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<Admin>("GET", "/auth/me"),
  updateMe: (display_name: string) => request<Admin>("PATCH", "/auth/me", { display_name }),
  totpSetup: () => request<{ secret: string; otpauth_uri: string }>("POST", "/auth/totp/setup"),
  totpEnable: (code: string) => request<Admin>("POST", "/auth/totp/enable", { code }),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("POST", "/auth/password", { current_password, new_password }),

  // Signed-in devices and remembered devices
  sessions: () => request<SignedInSession[]>("GET", "/auth/sessions"),
  signOutSession: (id: string) => request<void>("DELETE", `/auth/sessions/${encodeURIComponent(id)}`),
  signOutOthers: () => request<void>("POST", "/auth/sessions/sign-out-others"),
  rememberedDevices: () => request<RememberedDevice[]>("GET", "/auth/remembered-devices"),
  forgetDevice: (id: string) => request<void>("DELETE", `/auth/remembered-devices/${encodeURIComponent(id)}`),

  // Passkeys (see passkeys.ts for the browser side)
  passkeys: () => request<Passkey[]>("GET", "/auth/passkeys"),
  passkeyRegisterOptions: () => request<PasskeyOptions>("POST", "/auth/passkeys/register/options"),
  passkeyRegisterVerify: (challenge_id: string, credential: unknown, name?: string) =>
    request<Passkey>("POST", "/auth/passkeys/register/verify", { challenge_id, credential, name: name || null }),
  passkeyLoginOptions: () => request<PasskeyOptions>("POST", "/auth/passkeys/login/options"),
  passkeyLoginVerify: (challenge_id: string, credential: unknown) =>
    request<LoginResult>("POST", "/auth/passkeys/login/verify", { challenge_id, credential }),
  passkeyReverifyOptions: () => request<PasskeyOptions>("POST", "/auth/passkeys/reverify/options"),
  passkeyReverifyVerify: (challenge_id: string, credential: unknown) =>
    request<void>("POST", "/auth/passkeys/reverify/verify", { challenge_id, credential }),
  renamePasskey: (id: string, name: string) => request<Passkey>("PATCH", `/auth/passkeys/${encodeURIComponent(id)}`, { name }),
  removePasskey: (id: string) => request<void>("DELETE", `/auth/passkeys/${encodeURIComponent(id)}`),

  // Devices
  parents: () => request<Parent[]>("GET", "/parents"),
  invites: () => request<Invite[]>("GET", "/parents/invites"),
  createInvite: (email: string) => request<{ invite: Invite; link: string }>("POST", "/parents/invites", { email }),
  cancelInvite: (id: string) => request<void>("DELETE", `/parents/invites/${encodeURIComponent(id)}`),
  removeParent: (id: string) => request<void>("DELETE", `/parents/${encodeURIComponent(id)}`),
  lookupInvite: (token: string) => request<InviteInfo>("POST", "/invites/lookup", { token }),
  acceptInvite: (token: string, display_name: string, password: string) =>
    request<Admin>("POST", "/invites/accept", { token, display_name, password }),

  notificationSettings: () => request<NotificationSettings>("GET", "/notifications/settings"),
  saveNotificationSettings: (body: NotificationSettingsIn) => request<NotificationSettings>("PUT", "/notifications/settings", body),
  subscribePush: (body: { endpoint: string; keys: { p256dh: string; auth: string } }) =>
    request<void>("POST", "/notifications/push-subscriptions", body),
  unsubscribePush: (endpoint: string) => request<void>("POST", "/notifications/push-subscriptions/remove", { endpoint }),
  testNotification: () => request<{ email: boolean; push: number; email_error: string | null }>("POST", "/notifications/test"),
  recentNotifications: () => request<SentNotification[]>("GET", "/notifications?limit=30"),

  keepLive: (id: string) => request<void>("POST", `/devices/${encodeURIComponent(id)}/live`),
  processes: (id: string) => request<ProcessesReport>("GET", `/devices/${encodeURIComponent(id)}/processes`),
  stopApp: (id: string, body: { path: string; name: string; grace_seconds: number }) =>
    request<DeviceCommand>("POST", `/devices/${encodeURIComponent(id)}/apps/stop`, body),
  commands: (id: string, kind?: string) =>
    request<DeviceCommand[]>("GET", `/devices/${encodeURIComponent(id)}/commands${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`),
  command: (id: string, commandId: string) =>
    request<DeviceCommand>("GET", `/devices/${encodeURIComponent(id)}/commands/${encodeURIComponent(commandId)}`),

  appCatalog: () => request<AppCatalog>("GET", "/apps/catalog"),
  addCustomApp: (body: { name: string; category: string; exe?: string; product?: string; path?: string }) =>
    request<CatalogApp>("POST", "/apps/custom", body),
  removeCustomApp: (id: string) => request<void>("DELETE", `/apps/custom/${encodeURIComponent(id.replace(/^custom:/, ""))}`),
  appRules: (id: string) => request<AppRule[]>("GET", `/devices/${encodeURIComponent(id)}/app-rules`),
  putAppRules: (id: string, rules: AppRuleIn[]) => request<AppRule[]>("PUT", `/devices/${encodeURIComponent(id)}/app-rules`, { rules }),
  usage: (id: string, days = 7) => request<UsageDay[]>("GET", `/devices/${encodeURIComponent(id)}/usage?days=${days}`),
  seenApps: (id: string) => request<SeenApp[]>("GET", `/devices/${encodeURIComponent(id)}/apps/seen`),

  devices: () => request<Device[]>("GET", "/devices"),
  device: (id: string) => request<Device>("GET", d(id)),
  createDevice: (name: string, timezone: string) => request<EnrollmentCode>("POST", "/devices", { name, timezone }),
  updateDevice: (id: string, patch: { name?: string; timezone?: string; mode?: DeviceMode }) => request<Device>("PATCH", d(id), patch),
  deleteDevice: (id: string) => request<void>("DELETE", d(id)),
  reissueEnrollmentCode: (id: string) => request<EnrollmentCode>("POST", `${d(id)}/enrollment-code`),
  downloadInstaller,

  schedule: (id: string) => request<{ entries: ScheduleEntry[] }>("GET", `${d(id)}/schedule`),
  putSchedule: (id: string, entries: ScheduleEntry[]) => request<{ entries: ScheduleEntry[] }>("PUT", `${d(id)}/schedule`, { entries }),
  settings: (id: string) => request<DeviceSettings>("GET", `${d(id)}/settings`),
  patchSettings: (id: string, patch: SettingsPatch) => request<DeviceSettings>("PATCH", `${d(id)}/settings`, patch),
  setPin: (id: string, pin: string) => request<void>("PUT", `${d(id)}/pin`, { pin }),
  clearPin: (id: string) => request<void>("DELETE", `${d(id)}/pin`),

  overrides: (id: string, includePast = false) =>
    request<Override[]>("GET", `${d(id)}/overrides${includePast ? "?include_past=true" : ""}`),
  createOverride: (id: string, body: { duration_minutes?: number; ends_at?: string; reason?: string }) =>
    request<Override>("POST", `${d(id)}/overrides`, body),
  cancelOverride: (id: string, overrideId: string) => request<void>("DELETE", `${d(id)}/overrides/${encodeURIComponent(overrideId)}`),

  message: (id: string) => request<Message | null>("GET", `${d(id)}/message`),
  sendMessage: (id: string, body: string, mode: Message["mode"]) => request<Message>("POST", `${d(id)}/message`, { body, mode }),
  clearMessage: (id: string) => request<void>("DELETE", `${d(id)}/message`),

  lock: (id: string) => request<LockRequest | null>("GET", `${d(id)}/lock`),
  requestLock: (id: string, reason?: string) => request<LockRequest>("POST", `${d(id)}/lock`, { reason: reason || null }),
  clearLock: (id: string) => request<void>("DELETE", `${d(id)}/lock`),

  events: (q: EventQuery = {}) => request<ActivityEvent[]>("GET", `/events${query(q)}`),
};
