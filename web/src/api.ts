// Typed client for the Kidputer Control API. Types mirror server/app/schemas.py.
// The dashboard is served from the same origin as the API, so the session cookie is sent automatically.

export interface Admin {
  id: string;
  email: string;
  totp_enabled: boolean;
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
  ) {
    super(message);
  }
}

export const UNAUTHORIZED_EVENT = "kc:unauthorized";

// Required by the API on state-changing requests (CSRF defence; see server/app/deps.py).
const CSRF_HEADER = "X-Kidputer-Control";

function detailMessage(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
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
    throw new ApiError(res.status, detailMessage(parsed, res.status));
  }
  return parsed as T;
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
  loginMfa: (code: string) => request<LoginResult>("POST", "/auth/login/mfa", { code }),
  logout: () => request<void>("POST", "/auth/logout"),
  me: () => request<Admin>("GET", "/auth/me"),
  totpSetup: () => request<{ secret: string; otpauth_uri: string }>("POST", "/auth/totp/setup"),
  totpEnable: (code: string) => request<Admin>("POST", "/auth/totp/enable", { code }),
  changePassword: (current_password: string, new_password: string) =>
    request<void>("POST", "/auth/password", { current_password, new_password }),

  // Devices
  devices: () => request<Device[]>("GET", "/devices"),
  device: (id: string) => request<Device>("GET", d(id)),
  createDevice: (name: string, timezone: string) => request<EnrollmentCode>("POST", "/devices", { name, timezone }),
  updateDevice: (id: string, patch: { name?: string; timezone?: string }) => request<Device>("PATCH", d(id), patch),
  deleteDevice: (id: string) => request<void>("DELETE", d(id)),
  reissueEnrollmentCode: (id: string) => request<EnrollmentCode>("POST", `${d(id)}/enrollment-code`),

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
