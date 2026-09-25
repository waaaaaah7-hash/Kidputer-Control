# Windows agent

Not built yet (build steps 2–3). First, copy the V1 files into this folder unchanged, so the port
to V2 shows up as a normal diff:

- `Runtime.ps1`: the lock screen, countdown, PIN entry, and message windows. This becomes the interactive UI agent.
- `Modules/SessionMonitor.Common.psm1`: PIN hashing and lockout, tamper checksums, logging, and config I/O.
- `Install.ps1`, `Uninstall.ps1`
- `settings.json` (as a schema reference only)
- `docs/README.md` (the V1 docs)

`AdminConsole.ps1` and `Setup/*.ps1` (WinRM) are retired in V2 and don't need to come over.

The agent's side of the API contract is in `../docs/V2-ARCHITECTURE.md` under "Agent sync protocol".
