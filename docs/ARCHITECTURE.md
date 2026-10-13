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
| 1.1: modes, notifications, running apps, auto-update | **Done**. See the sections below. |

**Verification:**
- The API has full test coverage against Postgres.
- The agent's decision, PIN, queue, inbox, and tamper logic has unit tests.
- The agent's HTTP code has an integration test against a live API.
- The dashboard has an end-to-end test in Chromium against a live API. It covers sign-in, 2FA
  setup, adding a PC, and every device action, and checks that nothing overflows at phone width.
- Every script parses, and the Win32 interop compiles.
- CI runs the agent tests on Windows under both PowerShell 5.1 and 7.
- **Not yet exercised on a real PC:** the parts that need an interactive Windows session. These
  are the WinForms screens, `CreateProcessAsUser`, the scheduled task, the folder ACLs, closing
  apps, the shutdown listener, and swapping in an update.
  Before relying on the agent, walk through the checklist in `agent/README.md` once on the actual
  PC.

## Decisions made in step 1

### Dashboard authentication
- **Passwords:** argon2id (`argon2-cffi`), at least 12 characters. There is no public sign-up.
  The first admin comes from `KC_BOOTSTRAP_ADMIN_*` or `python -m app.cli create-admin <email>`;
  further parents join by invite (below).
- **2FA:** TOTP (`pyotp`), which works with any authenticator app. It is **mandatory** by default
  (`KC_REQUIRE_TOTP=true`). An admin who has only entered a password can reach just the TOTP
  setup endpoints. A code can be off by one 30-second step, and each code works only once.
  `python -m app.cli reset-totp` is the recovery path.
- **Sessions:** server-side session rows. The browser holds an opaque random token in an
  `HttpOnly; Secure; SameSite=Strict` cookie scoped to `/api`, and only its SHA-256 is stored.
  Login happens in two stages: the password creates an `mfa_pending` session that lasts 5 minutes
  and can do nothing else, then the TOTP code swaps it for an `active` session that lasts 12 hours.
- **Passkeys (WebAuthn, `webauthn` library):** sign in with Face ID, a fingerprint, or Windows
  Hello instead of the password and code.
  - **Counts as two factors on its own:** every passkey ceremony requires *user verification*
    (the fingerprint, face, or device PIN), and the server rejects responses without it.
  - **Discoverable:** the parent doesn't type an email; the browser offers the passkey for this
    site.
  - **One-time challenges:** stored server-side, used once, valid for 5 minutes.
  - **Clone detection:** a signature counter that goes backwards is refused.
  - **Bound to the dashboard's domain:** a custom domain means adding passkeys again.
  - **Backup:** the password plus authenticator code stays as the fallback.
- **Remember this device:** an optional checkbox on the code step.
  - It sets a separate, revocable `HttpOnly` cookie, sent only to `/api/v1/auth`, valid for 30 days
    (`KC_REMEMBER_DEVICE_DAYS`).
  - On that browser, a correct password alone starts a session, but that session isn't
    *verified* (below).
  - Changing the password, or `reset-totp`, forgets every remembered device.
- **Confirm it's you before risky actions:** each session records when a second factor (TOTP or
  passkey) was last used.
  - **Covered actions:** deleting a PC, setting or clearing its PIN, issuing an enrollment code
    or installer, adding or removing passkeys, and inviting or removing a parent.
  - **Rule:** these need that check within the last 10 minutes (`KC_REVERIFY_MINUTES`).
    Otherwise the API answers `403 {"code": "reauth_required"}`, and the dashboard asks for a
    passkey or code and retries.
- **Signed-in devices:** the Account page lists every live session and remembered device, with a
  readable browser and OS name, the sign-in method, last activity, and IP address. Any of them can
  be signed out; forgetting a remembered device also ends the sessions started with it.
- **CSRF:** SameSite=Strict, and every state-changing admin call must carry an
  `X-Kidputer-Control` header. Browsers can't add that header cross-site without a CORS preflight,
  and we only grant preflights to the dashboard's own origin.
- **Brute force:** 5 failed password or TOTP attempts lock the account for 15 minutes. An unknown
  email and a wrong password give the same response and take the same time.
- **Scope:** a single household. Every admin (parent) can see and change every device. Add tenancy
  only if this ever serves more than one family.
- **Several parents** (`server/app/routers/parents.py`):
  - **Invites:** a signed-in parent enters an email and gets a one-time link,
    `<site>/invite#<token>`. The token sits after the `#`, so browsers never send it to the
    server in the address or write it to logs; the page posts it in the request body. Only its
    SHA-256 is stored (`admin_invites`). It works once, expires after 48 hours
    (`KC_INVITE_TTL_HOURS`), and a newer invite for the same email cancels older ones. The app
    doesn't send email: the parent passes the link on themselves.
  - **Accepting:** the new parent picks a name and password and is signed in with a password-only
    session, so, as with any first sign-in, only TOTP setup works until 2FA is on.
  - **Removing:** sets `admin_users.disabled_at`, revokes their sessions and remembered devices,
    and deletes their passkeys. The row stays so the activity log keeps their name. A removed
    parent's sign-in gets the same answer as an unknown email. Inviting the same email again
    brings the account back with a new password and no 2FA, which they then set up again. A
    parent can't remove themselves.
  - Creating an invite and removing a parent are *confirm it's you* actions.
  - **Names:** `admin_users.display_name`, editable on the Account page; without one, the part of
    the email before `@` is shown. The events API adds `actor_name` to parent actions.

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
| — | `devices`, `admin_users`, `admin_sessions` | Devices and dashboard accounts. A removed parent keeps their `admin_users` row with `disabled_at` set. |
| parent invites | `admin_invites` | One-time sign-up links: token hash, expiry, accepted or cancelled time. |
| notifications | `notification_settings`, `push_subscriptions`, `notifications`, `app_secrets` | Each parent's levels, quiet hours and digest time; their phones and browsers; the send queue (kept 30 days); the Web Push key. |
| commands and reports | `device_commands`, `device_reports` | Parent-to-PC commands with their results; the latest bulk report per kind. |
| — | `devices.mode`, `.silent_since`, `.last_silence_started`, `.live_until`; `device_settings.silent_alert_minutes`, `.show_window_titles`, `.auto_update` | Modes, "PC went silent", live mode, and per-PC options. |

Migrations live in `server/migrations` (Alembic). CI runs `alembic check` to catch drift between
the models and the migrations.

## Agent sync protocol

All endpoints are under `/api/v1/agent`. Every call except `enroll` needs the device token.

| Call | Purpose |
|---|---|
| `POST /enroll` `{enrollment_code, hostname, agent_version}` | → `{device_id, device_token}` |
| `GET /state` (send `If-None-Match`) | Full desired-state snapshot, or `304`. The `ETag` is a hash of the snapshot. `X-Server-Time` lets the agent detect clock skew. |
| `POST /events` `{events:[…≤500]}` | Flush the local event queue. Returns `{accepted, duplicates}`. Safe to retry. |
| `POST /heartbeat` `{agent_version, status:{…}}` | Free-form live status shown in the dashboard, for example `locked`, `ui_agent_alive`, `child_is_admin` or `agent_bundle`. |
| `POST /reports/{kind}` | The latest copy of a bulk report (`processes`). Size-capped per kind. |
| `POST /commands/{id}/result` `{status, result}` | Outcome of a dashboard command. Retries are ignored once recorded. |
| `GET /update/manifest`, `GET /update/file?path=` | Auto-update: the agent files this server ships, with SHA-256s, and each file. |

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
  "immediate_lock": {"id": "…", "reason": "…", "requested_at": "…"},
  "mode": "managed", "show_window_titles": false,
  "live_until": "…" ,
  "commands": [{"id": "…", "kind": "stop_app", "payload": {"path": "…", "name": "Minecraft", "grace_seconds": 60}}],
  "agent_update": {"version": "1.1.0", "bundle": "<sha256 fingerprint>"}
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
- **Power:** `pc.shutting_down`, `pc.resumed`; `agent.started` carries `boot_time`
- **Apps:** `app.stop_warning`, `app.stopped`
- **Updates:** `agent.updated`, `agent.update_failed`

The server adds its own `system` events: `device.silent`, `device.offline`, `device.back_online`,
and `device.agent_was_stopped`.

## Commands, reports and live mode

- **Commands** (`device_commands`) are things a parent asks the PC to do. Pending ones ride in the
  state snapshot, so they reach the PC however it reaches the server. The agent carries each out
  once (handled ids survive restarts) and posts the result, retrying until it's recorded.
- **Reports** (`device_reports`) hold the latest bulk data from the PC, one row per kind.
- **Live mode:** a page showing live data pings `POST /devices/{id}/live` every 20 seconds. The
  snapshot's `live_until` (rounded to 30 s so the ETag stays stable) puts the agent on a 3-second
  poll. It also polls fast while any command is pending.

**Running apps (#1) and closing them (#2):** the watchdog lists processes in the child's session
(path via `QueryFullProcessImageName`, friendly names from the file's version info) and reports
them every 5 s while live, every 10 minutes otherwise. Window titles are only included when the
parent turns them on. A `stop_app` command shows the child a countdown (default 60 s); then the
UI agent, which can reach windows in the child's session, asks the app to close so it can save,
and the watchdog force-closes whatever is still running 5 s later. Processes under the Windows
folder, Windows' own process names, and Kidputer Control's files can never be closed.

## Notifications (#10)

- **Queue:** agent and system events map to a category (`server/app/notify.py`). Each parent has a
  level per category (immediate, daily digest, off) and gets a `notifications` row.
- **Sending:** a background thread (`server/app/jobs.py`, every 30 s, guarded by a Postgres
  advisory lock) sends immediate items grouped per parent, PC and category: it waits 20 s to
  gather a burst, and 5 minutes when that group alerted in the last 10. Quiet hours hold items
  until they end. Digests go out once a day at each parent's hour.
- **Channels:** email over SMTP (`KC_SMTP_*`; off when unset) and Web Push with VAPID (RFC 8292).
  The signing key is generated once and kept in `app_secrets`. Push payloads are end-to-end
  encrypted for the browser. Gone subscriptions (404/410) are deleted. The dashboard is a PWA
  (`manifest.webmanifest`, `sw.js`), which iPhones need for push.
- **"PC went silent":** the job flags enrolled PCs that haven't checked in for
  `silent_alert_minutes` (default 15, 0 = off). If the agent's last event before going quiet was
  `pc.shutting_down` (sent by a hidden-window listener for Windows' shutdown message), it's a
  quiet `device.offline` instead. On return, `device.back_online` is logged, and if the restarted
  agent's `boot_time` is before the silence began, Windows was running while Kidputer Control
  wasn't: a critical `device.agent_was_stopped`. A watchdog that was only asleep reports
  `pc.resumed` instead, which clears it.

## App rules (#3, #4) and usage time (#5)

- **Catalogue** (`server/app/catalog.py`): common apps in four categories, each with matchers. A
  matcher's fields must all match: `exe` (the file name *or* the original file name stored in the
  program, so renamed copies still match), and text in the `product`, `company`, `path` or
  `cmdline` (Minecraft Java runs on a generic `javaw.exe`). Parents' own apps (`custom_apps`) use
  the same shape, are shared by every PC, and are checked first.
- **Rules** (`app_rules`): per PC, per app or category: `allow`, `block`, or `schedule` with
  weekly windows in the PC's time zone. The snapshot carries the catalogue and the rules (an app's
  own rule first, so it beats its category's), with allowed times expanded into UTC instants for
  14 days, like bedtime. Offline for longer, the agent falls back to the weekly times.
- **Enforcement** (watchdog, every second): processes in the child's session are matched; one
  outside its allowed time that has just started is closed at once, with a notice on screen
  saying when it's available (`app.blocked`, at most every 10 minutes per app). One that was
  allowed when it started gets the same countdown as a parent's Close (`app.time_up`).
- **Usage:** the UI agent samples the foreground window every 5 seconds, skipping idle time
  (2 minutes without input) and the lock screen, and sends a minute's totals through the inbox.
  The watchdog caps and attributes them to apps, keeps per-day running totals in
  `config\usage.json`, and reports them (`POST /reports/usage`, every 5 minutes, 30 s while live).
  Days are the PC's time zone, from the snapshot's `day_starts`. The `_total` key is all screen
  time. Usage comes from the child's own session, so a determined child could under-report it;
  it's for insight, not enforcement.

## Change monitoring (#17)

`KidputerControl.Monitor.psm1`, run from the watchdog, each check on its own timer. What it
remembers lives in the SYSTEM-only `config\monitor.json`; the first sighting of each list is a
baseline, not a change.

| Check | Every | Events |
|---|---|---|
| New processes in the child's session | 2 s | `program.first_run` (with where it ran from: straight out of an archive, a USB drive, Downloads, temp), `archive.opened` for 7-Zip/WinRAR/PeaZip/Bandizip |
| Removable drives | 10 s | `usb.connected`, with a count of programs and archives in the top two folder levels |
| Installed programs (uninstall registry: machine, 32-bit, and the child's own while signed in) | 1 min | `software.installed`, `software.uninstalled` |
| Store apps for the child | 10 min | same |
| Local accounts and the Administrators group | 2 min | `account.created`, `.deleted`, `.admin_added`, `.admin_removed` |
| Defender real-time protection | 5 min | `defender.disabled`, `defender.enabled` |
| Archives in Downloads, Desktop, Documents; Recent Items | 5 min | `archive.new`, `archive.opened` |

- **First runs** key on the program's path with version-numbered folders made generic, so app
  updates aren't "new". For the first 48 hours after monitoring starts, programs are learned
  quietly. Memory is capped at 5,000 programs.
- **VPN and remote-access apps** are recognised by name or publisher, on install or first run, and
  also raise `software.vpn_detected`.
- Archive *contents* are never read: only names, sizes and where things ran from.

## Managed and Trusted modes (#16)

`devices.mode` is `managed` (standard child account) or `trusted` (administrator child account).
The installer download carries the mode: Managed refuses an administrator account, Trusted
accepts one. The watchdog reports `child_is_admin` every 10 minutes and only raises the critical
`agent.child_is_admin` event in Managed mode. Trusted mode relies on notifications, above all
"PC went silent", which an administrator child can't suppress from the PC.

## Agent auto-update (#13)

- **What's offered:** the agent folder inside the server's deployed image (`KC_AGENT_DIR`). There's
  no API to upload agent code, so a stolen parent password can't push code to a PC. The bundle
  fingerprint is a SHA-256 over sorted `path:sha256` lines (`server/app/agent_bundle.py`, mirrored
  by `Get-KcBundleFingerprint`; a shared test vector keeps them identical).
- **When:** the snapshot's `agent_update.bundle` differs from the installed files' fingerprint, and
  auto-update is on for the PC (default). At most one attempt an hour.
- **How:** download the manifest, check it is the expected bundle, download each file into the
  SYSTEM-only `config\update` folder and check its hash, back up the current files, copy the new
  ones over (never leaving an empty folder), remove files no longer shipped, check the result,
  and roll back on any failure. Then rewrite the tamper manifest, log `agent.updated`, and
  restart through the scheduled task.

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
  network can't delay relaunching the UI for long. The cycle is state → events → reports and
  command results → heartbeat.
- **Modules:** `KidputerControl.Common.psm1` (decision, PIN, queue, HTTP, installer helpers),
  `KidputerControl.Apps.psm1` (process lists, which processes may be closed), and
  `KidputerControl.Update.psm1` (fingerprints, verified download, install with rollback).

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
