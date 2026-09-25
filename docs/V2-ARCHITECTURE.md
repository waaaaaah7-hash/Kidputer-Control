# Session Monitor V2 — Architecture

This is the working design document for V2. The original handoff brief's goals and confirmed
decisions still apply: FastAPI + Postgres, cloud-only remote admin (WinRM retired), a
PowerShell SYSTEM watchdog built in this phase, fail-safe offline enforcement, and
`LockWorkStation` only.

```
 Browser (dashboard) ──HTTPS, session cookie──▶ FastAPI ──▶ Postgres
                                                  ▲
 Child PC:  SYSTEM watchdog ──HTTPS, device token─┘
               │  CreateProcessAsUser
               ▼
            UI agent (WinForms lock screen / countdown / messages) in the child's session
```

## Build status

| Step | Status |
|---|---|
| 1. Postgres schema + FastAPI backend + auth | **Done** (`server/`) |
| 2. Point agent config/log I/O at the API, with a local cache | Next. Needs the V1 scripts added to `agent/` |
| 3. SYSTEM watchdog + session launch; retire the logon Scheduled Task | Not started |
| 4. Web dashboard | Not started |

## Decisions made in step 1

### Dashboard authentication
- **Passwords:** argon2id (`argon2-cffi`), at least 12 characters. There is no public sign-up.
  Admins are created with `python -m app.cli create-admin <email>`.
- **2FA:** TOTP (`pyotp`), which works with any authenticator app. It is **mandatory** by default
  (`SM_REQUIRE_TOTP=true`). An admin who has only entered a password can reach just the TOTP
  setup endpoints. A code can be off by one 30-second step, and each code works only once.
  `python -m app.cli reset-totp` is the recovery path.
- **Sessions:** server-side session rows. The browser holds an opaque random token in an
  `HttpOnly; Secure; SameSite=Strict` cookie scoped to `/api`, and only its SHA-256 is stored.
  Login happens in two stages: the password creates an `mfa_pending` session that lasts 5 minutes
  and can do nothing else, then the TOTP code swaps it for an `active` session that lasts 12 hours.
- **CSRF:** SameSite=Strict, and every state-changing admin call must carry an
  `X-Session-Monitor` header. Browsers can't add that header cross-site without a CORS preflight,
  and we only grant preflights to the dashboard's own origin.
- **Brute force:** 5 failed password or TOTP attempts lock the account for 15 minutes. An unknown
  email and a wrong password give the same response and take the same time.
- **Scope:** a single household. Every admin can see every device. Add tenancy only if this ever
  serves more than one family.

### Device (agent) authentication
1. The admin creates a device in the dashboard and gets a one-time **enrollment code**
   (`XXXX-XXXX-XXXX`, about 60 bits, valid for 24 hours). You type this code in during the manual
   install.
2. The installer calls `POST /api/v1/agent/enroll` and receives a long-lived **device token**.
   The server stores only its hash.
3. The agent sends `Authorization: Bearer <token>` on every call. It should store the token with
   DPAPI (`LocalMachine` scope) in a folder that only SYSTEM and Administrators can read.
4. Reissuing an enrollment code revokes the current token. Use this to reinstall, rotate the
   token, or cut off a device.

### Lock-screen PIN
The PIN stays a secondary, local factor. The server hashes it with **PBKDF2-HMAC-SHA256** at
200k iterations with a 16-byte salt and syncs the hash to the agent, so the agent can check it
**offline**. The agent verifies it in PowerShell 5.1 or 7 like this:

```powershell
$kdf  = [Security.Cryptography.Rfc2898DeriveBytes]::new($pin, $salt, $iterations, [Security.Cryptography.HashAlgorithmName]::SHA256)
$ok   = [Security.Cryptography.CryptographicOperations]::FixedTimeEquals($kdf.GetBytes(32), $hash)  # PS7; on 5.1 compare in constant time manually
```

Because a 4–10 digit PIN is brute-forceable by anyone who can read the hash, the agent's local
cache **must** have a SYSTEM-only ACL. Failed-attempt lockout is enforced locally by the agent,
using the `max_attempts` and `lockout_minutes` values it receives.

## Data model (`server/app/models.py`)

| V1 `settings.json` | V2 table | Notes |
|---|---|---|
| schedule | `schedule_entries` | One row per weekday: `lock_at` → `unlock_at` in the device's IANA time zone. `unlock_at <= lock_at` means the window crosses midnight. A missing row means no lock that night. |
| PIN | `device_settings.pin_*` | See above. |
| other settings | `device_settings` | `enforcement_enabled`, `countdown_seconds` (default 300), `poll_interval_seconds`, PIN lockout, `pin_override_minutes` |
| overrides | `overrides` | Absolute UTC windows. Stored persistently and cancelled with `cancelled_at`, never deleted. |
| customMessage | `messages` | `persistent` or `closable`. A partial unique index allows at most one live message per device. `displayed_at`/`dismissed_at` come from agent events and `cleared_at` from the admin. |
| immediateLock | `lock_requests` | At most one active per device. It **does not cancel** overrides: the agent applies it on top of them. It stays in force until an admin clears it. |
| log files | `activity_events` | Central log for agent, admin, and system events. `(device_id, client_event_id)` is unique, so agent retries are idempotent. |
| — | `devices`, `admin_users`, `admin_sessions` | New in V2. |

Migrations live in `server/migrations` (Alembic). CI runs `alembic check` to catch drift between
the models and the migrations.

## Agent sync protocol

All endpoints are under `/api/v1/agent`. Every call except `enroll` needs the device token.

| Call | Purpose |
|---|---|
| `POST /enroll` `{enrollment_code, hostname, agent_version}` | → `{device_id, device_token}` |
| `GET /state` (send `If-None-Match`) | Full desired-state snapshot, or `304`. The `ETag` is a hash of the snapshot. `X-Server-Time` lets the agent detect clock skew. |
| `POST /events` `{events:[…≤500]}` | Flush the local event queue. Returns `{accepted, duplicates}`. Safe to retry. |
| `POST /heartbeat` `{agent_version, status:{…}}` | Free-form live status shown in the dashboard, for example `locked` or `ui_agent_alive`. |

Snapshot shape:

```json
{
  "etag": "\"…\"", "server_time": "…", "device_id": "…", "name": "Kid PC", "timezone": "Europe/London",
  "enforcement_enabled": true, "countdown_seconds": 300, "poll_interval_seconds": 30,
  "schedule": [{"day_of_week": 0, "lock_at": "21:30", "unlock_at": "07:00"}],
  "pin": {"algorithm": "pbkdf2-sha256", "hash": "b64", "salt": "b64", "iterations": 200000,
          "max_attempts": 5, "lockout_minutes": 15, "override_minutes": 60},
  "overrides": [{"id": "…", "starts_at": "…Z", "ends_at": "…Z"}],
  "message": {"id": "…", "body": "…", "mode": "persistent"} ,
  "immediate_lock": {"id": "…", "reason": "…", "requested_at": "…"}
}
```

**How the agent works offline:**
- Poll `GET /state` every `poll_interval_seconds`.
- When the response is `200`, write the snapshot atomically to the local cache (SYSTEM-only ACL,
  covered by tamper checks).
- **Always enforce from the cache, never from a live response.** Only remote admin actions need
  a connection.
- Queue events locally, each with a fresh UUID `client_event_id`, and flush the queue whenever
  the API is reachable.

**Deciding whether to lock:**
1. An `immediate_lock` locks the PC, whatever overrides are active.
2. Otherwise, an active override (from the server or a local PIN grant) keeps it unlocked.
3. Otherwise, the PC locks when the current time falls inside a schedule window.

**Event types the server acts on:**

| Event | `data` | Effect |
|---|---|---|
| `message.displayed` | `{message_id}` | Sets `displayed_at` |
| `message.dismissed` | `{message_id}` | Sets `dismissed_at`, but only for `closable` messages. Persistent messages are only logged. |
| `lock.applied` | `{lock_request_id}` | Sets `applied_at`, so the dashboard can show the lock was delivered |

Any other `event_type` matching `^[a-z][a-z0-9_.]{0,63}$` is stored as is. Suggested names:
`session.locked`, `session.unlocked`, `countdown.started`, `pin.success`, `pin.failed`,
`pin.lockout`, `pin.override_granted`, `tamper.detected` (severity `critical`),
`ui_agent.restarted`, `agent.started`, `agent.offline_resync`.

## Open questions — recommendations

These are recommendations only; none of them is decided yet. None of them blocks step 2 or 3.

- **Hosting:** **Render** or **Railway**. Both run the included `server/Dockerfile` directly, offer
  managed Postgres with backups, and terminate TLS for you. Fly.io's Postgres needs more hands-on
  management, and Azure is more setup than a single-household app needs.
- **Frontend:** **React + TypeScript + Vite**, built as a static SPA and served from the **same
  origin** as the API (for example, `/` for the SPA and `/api` for the API). Same origin keeps the
  SameSite=Strict cookie working without CORS.
- **Watchdog host:** a **SYSTEM-context Scheduled Task**, not a native Service. A Windows Service
  has to be a binary that speaks the Service Control Manager protocol, so `powershell.exe` can't
  be registered as one without a wrapper (srvany/NSSM) or a compiled C# host. Both of those
  contradict the "stays PowerShell" decision. A SYSTEM task needs these settings:
  - Created by an admin in the root task folder, so standard users can't end, disable, or delete it.
  - An `AtStartup` trigger, `ExecutionTimeLimit = PT0S`, and `RestartCount`/`RestartInterval`
    for crash recovery.
  - A repeating trigger every 1 minute with `MultipleInstances = IgnoreNew` as a
    self-resurrection backstop.

  It installs with `Register-ScheduledTask` and removes cleanly with `Unregister-ScheduledTask`.

## Running locally

```bash
docker compose up --build          # API on http://localhost:8000, OpenAPI docs at /docs
docker compose exec api python -m app.cli create-admin you@example.com
```

Running the tests needs a Postgres the tests can own:

```bash
cd server && pip install -e ".[dev]"
export SM_TEST_DATABASE_URL=postgresql+psycopg://sm:sm@localhost:5432/sessionmonitor_test
pytest
```

Configuration uses `SM_*` environment variables (see `server/app/config.py`). In production,
set `SM_DATABASE_URL`, and set `SM_CORS_ORIGINS` only if the dashboard is served from a
different origin.
