# Windows agent

Runs on the child's Windows 11 PC and has two parts:

| Part | Runs as | Job |
|---|---|---|
| `Watchdog.ps1` | SYSTEM, from the scheduled task `\SessionMonitor\Watchdog` | Enforcement, PIN checks, tamper detection, cloud sync. Supervises the UI agent. |
| `UiAgent.ps1` | The child, in their own session | Shows the countdown, the lock screen with PIN entry, and messages. Calls `LockWorkStation`. |

A standard user can't stop, change, or delete the SYSTEM task or its process. If the child closes
or kills the UI agent, the watchdog relaunches it within about a second (up to 20 relaunches a
minute). Every relaunch after the first is logged as `ui_agent.restarted`.

## Install

Requirements:
- Windows 10 or 11 with Windows PowerShell 5.1, which is built in.
- An administrator account for the parent to install with.
- A **standard** (non-administrator) account for the child.

Steps:
1. In the dashboard, click **Add a PC**. It shows the enrollment code (valid for 24 hours) and the
   exact install command, including your dashboard's URL.
2. Copy this `agent` folder to the child's PC.
3. Open **PowerShell as administrator** on the child's PC and run:

   ```powershell
   Set-ExecutionPolicy -Scope Process Bypass
   .\Install.ps1 -ApiUrl https://session-monitor.onrender.com -EnrollmentCode ABCD-EFGH-JKMN -ChildUser Sam
   ```

   For a Microsoft account, use `-ChildUser MicrosoftAccount\sam@example.com`.

4. Set the schedule and lock-screen PIN in the dashboard. The PC picks them up within one poll
   interval, 30 seconds by default.

To upgrade, run `.\Install.ps1` again with no arguments. It keeps the existing enrollment. To move
the PC to a new device record, pass a new `-EnrollmentCode`.

To uninstall, run `.\Uninstall.ps1` as administrator (add `-KeepLogs` to keep the local logs).
Then delete the device in the dashboard.

## What goes where

| Path | ACL | Contents |
|---|---|---|
| `%ProgramFiles%\SessionMonitor\` | Users: read/execute | The scripts. Their SHA-256 hashes are checked every minute. |
| `%ProgramData%\SessionMonitor\config\` | SYSTEM + Administrators | `agent.json`, `device.token` (DPAPI), `state.json` (cached config, including the PIN hash), `local.json`, `queue.jsonl` (events not yet uploaded), `manifest.json` |
| `%ProgramData%\SessionMonitor\logs\` | SYSTEM + Administrators | `agent-YYYY-MM.log`, one file per month, 12 months kept |
| `%ProgramData%\SessionMonitor\ipc\ui-state.json` | Users: read | What the UI should show. Written only by the watchdog. |
| `%ProgramData%\SessionMonitor\ipc\inbox\` | Users: create files only | Requests from the UI agent. The watchdog validates each one, then deletes it. |

The UI agent runs as the child, so it never sees the PIN hash and makes no decisions. It drops a
PIN attempt into the inbox, and the watchdog checks it and applies the lockout. The watchdog also
refuses requests that aren't allowed, for example closing a *persistent* message.

## How it decides

The watchdog checks the rules below in order, using only the **cached** config, so enforcement
keeps working offline:

1. **Immediate lock** from the dashboard: lock now, with no countdown. Active overrides are not
   cancelled.
2. **Override** (from the dashboard, or granted locally by the PIN): stay unlocked until it ends.
3. **Schedule window:** show the countdown (default 5 minutes), then lock.

The server sends the schedule as exact UTC windows for the next 14 days. That keeps it correct
across daylight-saving changes, and the child can't escape it by changing the PC's time zone. If
the PC is offline for longer than 14 days, the agent falls back to reading the weekly schedule in
the PC's local time.

The countdown deadline is saved to disk, so a reboot or a killed process doesn't restart the
countdown.

While the PC is locked, the lock screen stays full screen with a PIN box. If the child signs back
in to Windows and nobody types a PIN, the workstation re-locks within 30 seconds.

A correct PIN unlocks for `pin_override_minutes`, which defaults to 60. It also releases an
immediate lock. Both results sync to the dashboard.

## First-run checklist (do this once on the real PC)

The Windows-only parts can't run in CI, so check them by hand after the first install. Do it with
a short schedule window, or an immediate lock, set from the dashboard.

1. **Task:** `Get-ScheduledTask -TaskPath \SessionMonitor\` shows `Watchdog` as *Running*.
   `%ProgramData%\SessionMonitor\logs\agent-*.log` shows `agent.started`, with no loop errors.
2. **UI launch:** sign in as the child. Within a few seconds a hidden `powershell.exe` is running
   as the child.
3. **Countdown:** set a window that starts in two minutes. The countdown box appears bottom-right
   and can't be closed.
4. **Lock:** when the countdown ends, Windows locks. Sign back in: the full-screen lock screen is
   shown, and Windows locks again about 30 seconds later.
5. **PIN:**
   - A wrong PIN shows "Wrong PIN".
   - After `max_attempts` wrong PINs, entry is locked out.
   - The correct PIN unlocks, and a "Lock-screen PIN" override appears in the dashboard.
6. **Kill test:** as the child, end the UI agent in Task Manager. It reappears within a second or
   two, and the dashboard log shows `ui_agent.restarted`. Ending the watchdog (SYSTEM) is refused.
7. **Messages:**
   - A *closable* message closes with OK, and the dashboard shows it dismissed.
   - A *persistent* message has no close button, ignores Alt+F4, and goes away only when you
     clear it from the dashboard.
8. **Immediate lock:** it locks at once with no countdown, even during an override. Clearing it
   from the dashboard unlocks the PC within one poll interval.
9. **Offline:** disconnect the network. Scheduled locking still happens. Reconnect: the queued
   events show up in the dashboard.
10. **Tamper:** as an administrator, edit a file under `%ProgramFiles%\SessionMonitor`. Within a
    minute a critical `tamper.detected` event appears. Re-run `Install.ps1` to restore the files.
11. **Parent account:** sign in as the parent. No countdown or lock screen appears.

## Tests

```powershell
.\tests\Parse.Tests.ps1     # every script parses; the Win32 interop compiles
.\tests\Common.Tests.ps1    # decision logic, countdown, PIN and lockout, queue, inbox, tamper
```

Both run on Windows PowerShell 5.1 and on PowerShell 7 (Windows CI runs both).
`Integration.Tests.ps1` runs the agent's HTTP code against a live API. It is driven by
`server/tests/test_agent_integration.py`.
