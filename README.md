# Session Monitor V2

Parental-control system for Windows 11. It enforces a nightly lock schedule with a countdown and a
PIN override, and shows custom messages and immediate locks. Tamper detection and activity
logging are built in. V2 moves admin to a cloud API and web dashboard, and adds a hardened SYSTEM
watchdog on the child's PC.

| Path | What |
|---|---|
| `server/` | FastAPI + Postgres cloud API: schema, admin auth (password + TOTP), device management, agent sync protocol |
| `agent/` | Windows agent (PowerShell 5.1+): SYSTEM watchdog, UI agent (lock screen, countdown, messages), installer. See [agent/README.md](agent/README.md). |
| `web/` | Parent dashboard: React + TypeScript (Vite), served by the API on the same origin |
| `docs/DEPLOY-RENDER.md` | Deploying to Render (Blueprint in `render.yaml`) and adding the first PC |
| `docs/V2-ARCHITECTURE.md` | Design, decisions, agent protocol, and build status |

**Getting started:**
1. Deploy the cloud side to Render: [docs/DEPLOY-RENDER.md](docs/DEPLOY-RENDER.md).
2. Install the agent on the child's PC: [agent/README.md](agent/README.md).

To run everything locally, use `docker compose up --build` and open http://localhost:8000. The
design is in [docs/V2-ARCHITECTURE.md](docs/V2-ARCHITECTURE.md).
