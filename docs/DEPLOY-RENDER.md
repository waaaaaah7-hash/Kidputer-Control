# Deploying to Render

The whole cloud side is **one Render web service** plus **one Render Postgres database**. The web
service runs the root `Dockerfile`, which builds the React dashboard and serves it from FastAPI.
The dashboard is at `/` and the API at `/api` on the same address, so the login cookie works
without any cross-origin setup.

## 1. Create the services from the Blueprint

1. Push this repository to GitHub. It is already there if you're reading this on GitHub.
2. In Render, choose **New → Blueprint** and pick this repository. Render reads `render.yaml`
   and proposes:
   - **kidputer-control**: a Docker web service on the `starter` plan, with health check `/healthz`
   - **kidputer-control-db**: Postgres on the `basic-256mb` plan
3. Render asks for the two values marked `sync: false`:
   - `KC_BOOTSTRAP_ADMIN_EMAIL`: your email
   - `KC_BOOTSTRAP_ADMIN_PASSWORD`: a strong password of at least 12 characters, which you'll
     change later anyway
4. Click **Apply**. The first deploy builds the image, runs the database migrations, starts the
   app, and creates your admin account because none exists yet.

**Why paid plans?**
- A free web service sleeps when idle. The PC would then see the API as offline. It keeps
  enforcing from its cache, but your changes would be delayed while the service wakes up.
- Free Postgres databases are deleted after 30 days.

You can change either plan in `render.yaml` or in the Render dashboard. Check Render's pricing
page for current costs.

## 2. First sign-in

1. Open the service URL, for example `https://kidputer-control.onrender.com`.
2. Sign in with the bootstrap email and password.
3. You'll be asked to set up two-factor sign-in. Scan the QR code with an authenticator app and
   enter the code.
4. **In Render, delete `KC_BOOTSTRAP_ADMIN_PASSWORD`** from the service's Environment, then save.
   It's only read while no admin exists, but it shouldn't stay stored.
5. Under **Account**, change your password.

To add a second parent, go to **Account → Parents**, enter their email, and send them the
one-time link it shows. It works once and expires after 48 hours. No Render Shell is needed.

## 3. Add the child's PC

1. In the dashboard, click **Add a PC**, name it, and pick its time zone. Then click **Download
   installer**. The zip already contains your dashboard's address and a one-time enrollment code.
2. On the child's PC, unzip it and double-click **Install Kidputer Control**. Approve the Windows
   permission prompt and pick the child's account. (Installing by hand is described in
   `agent/README.md`.)

3. Set the schedule and a lock-screen PIN on the device's page.
4. Walk through the first-run checklist in `agent/README.md` once.

## Custom domain (optional)

Add the domain in Render under **Settings → Custom Domains**. Render issues the TLS certificate.
The PC stores the address it was installed with, so after switching:
1. Open the dashboard on the new domain.
2. On the device's page, use **Reinstall or move to another PC → Download installer**. The
   download uses the address you opened the dashboard on.
3. Run the new installer on the PC.

Alternatively, set `KC_PUBLIC_URL` to the new address, so every installer uses it wherever the
dashboard was opened from.

Passkeys belong to the domain they were added on. After switching domains, sign in with your
password and code, then add your passkeys again from the Account page. Remembered devices also
need re-ticking on the new domain.

## Email notifications (optional)

Push notifications to your phone work without any setup: open **Account → Notification settings**
and tap **Turn on for this device**. On an iPhone, first add the dashboard to the Home Screen
(Share → Add to Home Screen) and open it from there.

For email alerts and the daily summary, connect an email service. Any service that offers SMTP
works, for example Resend, Postmark, Brevo, or Gmail with an
[app password](https://support.google.com/accounts/answer/185833). In the Render service's
**Environment**, add:

| Variable | Example |
|---|---|
| `KC_SMTP_HOST` | `smtp.gmail.com` |
| `KC_SMTP_PORT` | `587` |
| `KC_SMTP_USERNAME` | `you@gmail.com` |
| `KC_SMTP_PASSWORD` | the app password or API key |
| `KC_SMTP_FROM` | `Kidputer Control <you@gmail.com>` |

Save; Render restarts the service. Then use **Send a test** on the Notification settings page.

## Operations

- **Logs:** use the service's Logs tab in Render. The central activity log is in the dashboard
  under **Activity**.
- **Backups:** paid Render Postgres plans include automatic backups. See the database's Recovery
  tab.
- **Upgrades:** pushing to the default branch redeploys the service (Render auto-deploy).
  Migrations run automatically at start-up.
- **Lost authenticator:** from the Render Shell, run
  `python -m app.cli reset-totp you@example.com`. Then sign in and enroll again.

## Configuration reference

All settings are environment variables. Defaults are in `server/app/config.py`.

| Variable | Default | Notes |
|---|---|---|
| `KC_DATABASE_URL` | local dev URL | Set by the Blueprint. `postgres://` URLs are accepted. |
| `KC_BOOTSTRAP_ADMIN_EMAIL` / `_PASSWORD` | empty | First admin only (see above) |
| `KC_REQUIRE_TOTP` | `true` | Two-factor sign-in is mandatory |
| `KC_SESSION_TTL_HOURS` | `12` | Dashboard sign-in lifetime |
| `KC_REMEMBER_DEVICE_DAYS` | `30` | How long "Remember this device" lasts |
| `KC_REVERIFY_MINUTES` | `10` | How recent a passkey or code check must be before risky changes |
| `KC_COOKIE_SECURE` | `true` | Set `false` only for local plain-HTTP development |
| `KC_ENROLLMENT_CODE_TTL_HOURS` | `24` | How long an enrollment code stays valid |
| `KC_INVITE_TTL_HOURS` | `48` | How long a parent invite link stays valid |
| `KC_STATIC_DIR` | `/srv/static` in the image | Built dashboard |
| `KC_AGENT_DIR` | `/srv/agent` in the image | Agent files bundled into installer downloads |
| `KC_SMTP_HOST`, `KC_SMTP_PORT`, `KC_SMTP_USERNAME`, `KC_SMTP_PASSWORD`, `KC_SMTP_FROM` | empty, `587` | Email notifications (above). Email is off while the host is empty. |
| `KC_SMTP_SECURITY` | `starttls` | `starttls` (port 587), `ssl` (port 465), or `none` |
| `KC_PUBLIC_URL` | empty | Address written into installer downloads. Leave empty to use the address the dashboard was opened on; set it if you use a custom domain and want installers and invite links to use it. |
