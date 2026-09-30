# Kidputer Control — Architecture

This is the design document for Kidputer Control 1.0. Its core decisions:
- FastAPI + Postgres in the cloud.
- Remote admin only through the cloud dashboard.
- A PowerShell SYSTEM watchdog on the PC.
- Enforcement that keeps working offline.
- Locking only through `LockWorkStation`.

It grew out of an earlier local-only design that was never deployed. That design's feature set
carries over: schedule, countdown, PIN with lockout, persistent overrides, closable and
persistent messages, immediate lock, tamper detection, and logging. Nothing needs migrating.

App and process management is planned for a future version. That is why the product is
"Control" rather than a screen-time timer.

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
| 2. Agent config and logs go through the API, with a local cache | **Done** (`agent/KidputerControl/Modules/KidputerControl.Common.psm1`) |
| 3. SYSTEM watchdog + launching the UI into the child's session | **Done** (`agent/KidputerControl/Watchdog.ps1`, `UiAgent.ps1`, `Install.ps1`). See the verification note below. |
| 4. Web dashboard | **Done** (`web/`: React + TypeScript + Vite, served by FastAPI). Deploys to Render: see `docs/DEPLOY-RENDER.md`. |

**Verification:**
- The API has full test coverage against Postgres.
- The agent's decision, PIN, queue, inbox, and tamper logic has unit tests.
- The agent's HTTP code has an integration test against a live API.
- The dashboard has an end-to-end test in Chromium against a live API. It covers sign-in, 2FA
  setup, adding a PC, and every device action, and checks that nothing overflows at phone width.
- Every script parses, and the Win32 interop compiles.
- CI runs the agent tests on Windows under both PowerShell 5.1 and 7.
- **Not yet exercised on a real PC:** the parts that need an interactive Windows session. These
  are the WinForms screens, `CreateProcessAsUser`, the scheduled task, and the folder ACLs.
  Before relying on the agent, walk through the checklist in `agent/README.md` once on the actual
  PC.

## Decisions made in step 1

### Dashboard authentication
- **Passwords:** argon2id (`argon2-cffi`), at least 12 characters. There is no public sign-up.
  Admins are created with `python -m app.cli create-admin <email>`.
- **2FA:** TOTP (`pyotp`), which works with any authenticator app. It is **mandatory** by default
  (`KC_REQUIRE_TOTP=true`). An admin who has only entered a password can reach just the TOTP
  setup endpoints. A code can be off by one 30-second step, and each code works only once.
  `python -m app.cli reset-totp` is the recovery path.
- **Sessions:** server-side session rows. The browser holds an opaque random token in an
  `HttpOnly; Secure; SameSite=Strict` cookie scoped to `/api`, and only its SHA-256 is stored.
  Login happens in two stages: the password creates an `mfa_pending` session that lasts 5 minutes
  and can do nothing else, then the TOTP code swaps it for an `active` session that lasts 12 hours.
- **CSRF:** SameSite=Strict, and every state-changing admin call must carry an
  `X-Kidputer-Control` header. Browsers can't add that header cross-site without a CORS preflight,
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
3. The agent sends `Authorization: Bearer <token>` on every call. It stores the token with DPAPI
   (`LocalMachine` scope) in a folder that only SYSTEM and Administrators can read.
4. Reissuing an enrollment code revokes the current token. Use this to reinstall, rotate the
   token, or cut off a device.

### Lock-screen PIN
The PIN stays a secondary, local factor. The server hashes it with **PBKDF2-HMAC-SHA256** at
200k iterations with a 16-byte salt and syncs the hash to the agent, so the agent can check it
**offline**. The agent verifies it with .NET's `Rfc2898DeriveBytes` (see `Test-KcPin`).

A 4–10 digit PIN is brute-forceable by anyone who can read the hash. So:
- The hash exists only in the SYSTEM-only config folder.
- The SYSTEM watchdog does the PIN check. The UI agent, which runs as the child, only forwards
  what was typed.
- Failed-attempt lockout (`max_attempts`, `lockout_minutes`) is enforced by the watchdog, so the
  child can't reset it by restarting the UI.

## Data model (`server/app/models.py`)

| Concept | Table | Notes |
|---|---|---|
| schedule | `schedule_entries` | One row per weekday: `lock_at` → `unlock_at` in the device's IANA time zone. `unlock_at <= lock_at` means the window crosses midnight. A missing row means no lock that night. |
| PIN | `device_settings.pin_*` | See above. |
| other settings | `device_settings` | `enforcement_enabled`, `countdown_seconds` (default 300), `poll_interval_seconds`, PIN lockout, `pin_override_minutes` |
| overrides | `overrides` | Absolute UTC windows. Stored persistently and cancelled with `cancelled_at`, never deleted. |
| custom message | `messages` | `persistent` or `closable`. A partial unique index allows at most one live message per device. `displayed_at`/`dismissed_at` come from agent events and `cleared_at` from the admin. |
| immediate lock | `lock_requests` | At most one active per device. It **does not cancel** overrides: the agent applies it on top of them. It stays in force until an admin clears it or a parent enters the PIN at the lock screen. |
| activity log | `activity_events` | Central log for agent, admin, and system events. `(device_id, client_event_id)` is unique, so agent retries are idempotent. |
| — | `devices`, `admin_users`, `admin_sessions` | Devices and dashboard accounts. |

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
  "lock_windows": [{"start": "2026-01-12T21:30:00+00:00", "end": "2026-01-13T07:00:00+00:00"}],
  "lock_windows_until": "2026-01-26T12:00:00+00:00",
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

**Lock windows** are the weekly schedule expanded on the server into exact UTC instants for the
next 14 days, using the device's IANA time zone. This is done server-side for two reasons:
- Windows PowerShell 5.1 has no IANA time zone support.
- Standard users *can* change the PC's time zone, which would otherwise shift the schedule.

The agent only compares UTC instants. It falls back to evaluating `schedule` in PC-local time
only after `lock_windows_until` has passed, which means offline for longer than 14 days.

**Deciding whether to lock:**
1. An `immediate_lock` locks the PC, whatever overrides are active.
2. Otherwise, an active override (from the server or a local PIN grant) keeps it unlocked.
3. Otherwise, the PC locks when the current time falls inside a lock window.

**Event types the server acts on:**

| Event | `data` | Effect |
|---|---|---|
| `message.displayed` | `{message_id}` | Sets `displayed_at` |
| `message.dismissed` | `{message_id}` | Sets `dismissed_at`, but only for `closable` messages. Persistent messages are only logged. |
| `lock.applied` | `{lock_request_id}` | Sets `applied_at`, so the dashboard can show the lock was delivered |
| `lock.released_by_pin` | `{lock_request_id}` | Clears the immediate lock |
| `pin.override_granted` | `{starts_at, ends_at}` | Creates an override with reason "Lock-screen PIN". Windows longer than a day are rejected. |

Any other `event_type` matching `^[a-z][a-z0-9_.]{0,63}$` is stored as is. The agent emits:
- **Lifecycle:** `agent.started`, `agent.installed`, `agent.uninstalled`, `agent.reconnected`,
  `agent.unauthorized`, `agent.child_is_admin`
- **Locking:** `session.countdown_started`, `session.locked`, `session.unlocked`
- **PIN:** `pin.failed`, `pin.lockout`, `pin.rejected_locked_out`
- **Messages:** `message.close_blocked`
- **UI agent:** `ui_agent.restarted`, `ui_agent.error`
- **Integrity:** `tamper.detected` (critical), `tamper.resolved`, `clock.skew`

## Agent design (`agent/`)

```
 SYSTEM (session 0)                                   Child's session
 ┌──────────────────────────────┐   ui-state.json    ┌───────────────────────────┐
 │ Watchdog.ps1                 │ ─────────────────▶ │ UiAgent.ps1 (WinForms)    │
 │  decision from cached state  │                    │  countdown · lock screen  │
 │  PIN check + lockout         │ ◀───────────────── │  PIN box · messages       │
 │  cloud sync · tamper check   │   ipc\inbox\*.json │  LockWorkStation          │
 │  relaunch UI if it dies      │ ── CreateProcessAsUser ──▶                     │
 └──────────────────────────────┘                    └───────────────────────────┘
```

- **Hosting the watchdog:** a SYSTEM scheduled task (`\KidputerControl\Watchdog`), not a Windows
  Service. A Service has to be a binary that speaks the Service Control Manager protocol.
  `powershell.exe` can't be one without a wrapper (srvany/NSSM) or a compiled host, and either
  would break the "stays PowerShell" decision. The task is configured like this:
  - An `AtStartup` trigger, no execution time limit, and restart-on-failure.
  - A repeating 1-minute trigger with `IgnoreNew` as a backstop.
  - A `Global\` mutex that keeps it to one instance. Standard users lack
    `SeCreateGlobalPrivilege`, so they can't squat the mutex name.
  - Installed with `Register-ScheduledTask` and removed with `Unregister-ScheduledTask`.
- **UI launch:** a small C# class (`KidputerControl.Native.cs`, loaded with `Add-Type`) calls
  `WTSQueryUserToken`, `CreateProcessAsUser`, and `winsta0\default`. The watchdog only launches
  the UI when the console session belongs to the configured child account, so parents' sessions
  are left alone.
- **IPC:** files, not named pipes, because they are simpler and robust in PowerShell 5.1.
  - `ipc\ui-state.json` is replaced atomically by the watchdog. The child can read it but not
    write it.
  - In `ipc\inbox\` the child can create files but can't list or read them.
  - Everything in the inbox is treated as untrusted input: size-capped, validated, then deleted.
- **Sync pacing:** at most one HTTP call per 1-second tick, with a 10-second timeout. A slow
  network can't delay relaunching the UI for long.

## Hosting and dashboard

- **Hosting: Render.** A Blueprint (`render.yaml`) defines one Docker web service plus Render
  Postgres. The root `Dockerfile` builds the dashboard and the API into a single image. See
  `docs/DEPLOY-RENDER.md`.
- **Dashboard: React + TypeScript** (Vite), in `web/`. It is a static SPA served by FastAPI from
  the same origin as the API:
  - The SameSite=Strict session cookie works without CORS.
  - The API sends a strict Content-Security-Policy for the app: `'self'` only, no inline scripts.
  - Its only runtime dependencies are React, React Router, and `qrcode` (for the 2FA QR code).
- **First admin:** Render's free tier has no shell, so the first admin is created at start-up from
  `KC_BOOTSTRAP_ADMIN_EMAIL`/`KC_BOOTSTRAP_ADMIN_PASSWORD`, and only while no admin exists.

## Running locally

```bash
KC_BOOTSTRAP_ADMIN_EMAIL=you@example.com KC_BOOTSTRAP_ADMIN_PASSWORD='a long local password' \
  docker compose up --build        # dashboard + API on http://localhost:8000, API docs at /docs
```

For dashboard development with hot reload, run `cd web && npm install && npm run dev`. It proxies
`/api` to the API on port 8000.

Running the tests needs a Postgres the tests can own:

```bash
cd server && pip install -e ".[dev]"
export KC_TEST_DATABASE_URL=postgresql+psycopg://kc:kc@localhost:5432/kidputercontrol_test
pytest   # add KC_PWSH=<pwsh> for the agent integration test; build web/ + install playwright for the browser test
```

Configuration uses `KC_*` environment variables (see `server/app/config.py`). In production,
set `KC_DATABASE_URL`, and set `KC_CORS_ORIGINS` only if the dashboard is served from a
different origin.
