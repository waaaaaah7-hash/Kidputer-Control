# Kidputer Control

Parental control for Windows PCs. It has three parts:
- A FastAPI + Postgres cloud API (`server/`).
- A React + TypeScript parent dashboard (`web/`), served by the API on the same origin.
- A PowerShell agent on the child's PC (`agent/`): a SYSTEM watchdog plus a UI agent in the
  child's session.

Version 1.3.0. Read `docs/ARCHITECTURE.md` for the design, and `docs/ROADMAP.md` for what's
planned. The README is the user-facing overview.

## Workflow

- **Work on `DEV`.** `Main` is the default branch and what Render deploys. Never push to `Main`;
  the owner merges `DEV` into `Main` themselves.
- **Commit messages:** describe *what and why* in plain language. Run the relevant test suites
  before pushing.
- **Docs move with code:** when behaviour changes, update the README, `docs/ARCHITECTURE.md`,
  `docs/DEPLOY-RENDER.md`, and `agent/README.md`, and mark roadmap items done in
  `docs/ROADMAP.md`.
- **`README.pdf`:** whenever `README.md` changes, regenerate it with
  `python tools/render_readme_pdf.py` and commit both together.
- **The owner isn't a developer.** Explain in plain language: effort, feasibility, and practicality
  for proposals. Ask before large or irreversible changes. Planned features go in the roadmap, not
  straight into code.

## Running things

| What | Command |
|---|---|
| Server tests | `cd server && pip install -e ".[dev]" && KC_TEST_DATABASE_URL=postgresql+psycopg://kc:kc@localhost:5432/kidputercontrol_test pytest -q` |
| Migration drift | `KC_DATABASE_URL=<test db url> alembic upgrade head && alembic check` (in `server/`) |
| Dashboard | `cd web && npm ci && npm run typecheck && npm test && npm run build` |
| Agent unit tests | `powershell -File agent/tests/Common.Tests.ps1` and `agent/tests/Parse.Tests.ps1` (also with `pwsh`) |
| Whole stack locally | `KC_BOOTSTRAP_ADMIN_EMAIL=... KC_BOOTSTRAP_ADMIN_PASSWORD=... docker compose up --build` → http://localhost:8000 |
| Dashboard dev server | `cd web && npm run dev` (proxies `/api` to localhost:8000) |

**Server tests**
- They need a real Postgres that the tests can wipe: database `kidputercontrol_test`, user and
  password `kc`.
- Two tests are skipped unless their tools are available:
  - `test_agent_integration.py` needs `pwsh` (or set `KC_PWSH`). It runs the agent's PowerShell
    against a live API.
  - `test_dashboard_e2e.py` needs `pip install playwright` and a built `web/dist`. Set
    `KC_CHROMIUM_PATH` to use an existing Chromium, and `KC_E2E_SCREENSHOTS=<dir>` to save
    screenshots.
- The browser tests use `http://localhost`, not `127.0.0.1`, because passkeys (WebAuthn) refuse
  IP addresses.

**CI** (`.github/workflows/`) runs the server and browser tests on Ubuntu, the dashboard checks,
and the agent tests on Windows under both PowerShell 5.1 and 7.

## Conventions and traps

**Agent (PowerShell)**
- The SYSTEM task runs **Windows PowerShell 5.1**. No PS7-only syntax: no `??`, ternaries, `&&`,
  `-AsHashtable`, or `ConvertFrom-Json -Depth`.
- Agent files must be **plain ASCII**: 5.1 misreads BOM-less UTF-8, so even a stray em dash
  breaks things. `Parse.Tests.ps1` enforces this.
- Dates from JSON go through `ConvertTo-KcDate`: PS7 auto-converts ISO strings to `[datetime]`,
  5.1 doesn't.
- To pass a null string to .NET, use `[NullString]::Value`; a plain `$null` arrives as `""`.
- The embedded C# (`*.Native.cs`) must stay C# 5 (no `?.`, no `$""`), so 5.1's compiler accepts
  it.
- Exported functions use the `Kc` prefix (`Get-KcPaths`, `ConvertTo-KcDate`, ...).
- `.cmd` files need CRLF line endings. The installer launcher is generated in
  `server/app/installer.py`.

**Server**
- **Migrations:** create them with `alembic revision --autogenerate`. Then:
  - Name any constraint that autogenerate leaves unnamed, or the downgrade fails.
  - Check that upgrade, downgrade, and upgrade again all work.
- **Settings** are environment variables with the `KC_` prefix (`server/app/config.py`). Hosted
  Postgres URLs (`postgres://...`) are normalised automatically.
- **Risky admin actions** use `VerifiedAdminDep`, which requires a second-factor check (TOTP or
  passkey) within `KC_REVERIFY_MINUTES`. Otherwise it returns `403 {"code": "reauth_required"}`.
  New actions that affect the child's PC or sign-in should use it.
- **Passkeys** must keep `require_user_verification=True`. Without it, a passkey isn't
  multi-factor. There's a test that fails if this is weakened.
- **State-changing dashboard calls** need the `X-Kidputer-Control` header (CSRF defence).

**Dashboard**
- Same-origin only, with a strict CSP: no inline scripts or styles, and images from `'self'` or
  `data:` only.
- Use `ActionButton` for anything that calls the API. It handles busy states, error toasts, and
  the "Confirm it's you" retry. Raw `onSubmit` handlers that hit risky endpoints must wrap the
  call in `useReauth()`.
- Test that layouts work at phone width (390px). The browser test checks for content overflowing
  its card.
- Layout: a sidebar from 900px wide, and below that a top bar plus a fixed bottom menu bar. Both
  carry a `nav` named "Main"; only one is visible at a time, so tests can use
  `get_by_role("navigation", name="Main")` at any width.
- Colours are CSS variables at the top of `styles.css` (warm light and dark themes). Use them
  rather than new hex values, and define any new one in all three places: light `:root`, dark
  under `prefers-color-scheme` (guarded by `:root:not([data-theme="light"])`), and
  `:root[data-theme="dark"]`. The parent's Light/Dark choice (Account → Appearance) sets
  `data-theme` on `<html>` via `web/src/theme.ts`, stored in localStorage on that device only.
- The Help page (`/help`) links to the video guides' YouTube playlist. The link is in
  `web/src/help.ts` (and in README, docs/VIDEOS.md, DEPLOY-RENDER.md and agent/README.md).

**Security posture**
- The PIN hash and the device token live only in the SYSTEM-only config folder.
- The UI agent runs as the child, holds no secrets, and only drops requests into the inbox.
- Everything in the inbox is untrusted.
- Never add features that read or download the child's file *contents*, or log keystrokes or
  take screenshots. These were deliberately excluded (see the roadmap).

## Status

**Done**
- Cloud API with password + TOTP sign-in.
- Agent: schedule, countdown, PIN, overrides, messages, immediate lock, and tamper detection.
- Dashboard.
- Render deployment (`render.yaml`, `Dockerfile`).
- One-click installer download.
- Passkeys, remember this device, and confirm-it's-you for risky actions.
- Several parents: invite by one-time link, remove, and names in the activity log.
- 1.1: Managed and Trusted modes, notifications (email, Web Push, "PC went silent"), running apps
  and closing them, and agent auto-update.
- 1.2: change monitoring, app rules with a catalogue and custom apps, usage time, and the Files
  tab (drives, folder-size scans, live folder listing).
- 1.3: the dashboard redesign (warm look, phone-first home page, one page per PC), the Security
  tab (#23: Defender status, threats and scans; Windows updates installed automatically, with
  restarts at bedtime), and narrated video guides (`docs/VIDEOS.md`).

**Not yet verified on real hardware**
- Everything Windows-only: the WinForms lock screen, `CreateProcessAsUser`, the scheduled task,
  and folder ACLs.
- The first-run checklist in `agent/README.md` is roadmap item #0.
- When testing, use a VM or a dedicated test account. Installing enforces the schedule on the
  chosen account.

**Next:** see the release plan in `docs/ROADMAP.md`:
- 1.0.x: real-PC verification (now including the 1.1 checklist items).
- 1.4: install requests (Managed mode), daily limits, "ask for more time", schedule templates
  and holidays.
