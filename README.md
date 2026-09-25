# Session Monitor V2

Parental-control system for Windows 11. It enforces a nightly lock schedule with a countdown and a
PIN override, and shows custom messages and immediate locks. Tamper detection and activity
logging are built in. V2 moves admin to a cloud API and web dashboard, and adds a hardened SYSTEM
watchdog on the child's PC.

| Path | What |
|---|---|
| `server/` | FastAPI + Postgres cloud API: schema, admin auth (password + TOTP), device management, agent sync protocol |
| `agent/` | Windows agent (PowerShell 5.1+): SYSTEM watchdog, UI agent (lock screen, countdown, messages), installer. See [agent/README.md](agent/README.md). |
| `docs/V2-ARCHITECTURE.md` | Design, decisions, agent protocol, build status, and the recommendations still waiting on a decision |

Quick start: `docker compose up --build`, then open http://localhost:8000/docs. Full details are in
[docs/V2-ARCHITECTURE.md](docs/V2-ARCHITECTURE.md).
