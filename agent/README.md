# Windows agent

Runs on the child's Windows 11 PC and has two parts:

| Part | Runs as | Job |
|---|---|---|
| `Watchdog.ps1` | SYSTEM, from the scheduled task `\KidputerControl\Watchdog` | Enforcement, PIN checks, tamper detection, cloud sync. Supervises the UI agent. |
| `UiAgent.ps1` | The child, in their own session | Shows the countdown, the lock screen with PIN entry, and messages. Calls `LockWorkStation`. |

A standard user can't stop, change, or delete the SYSTEM task or its process. If the child closes
or kills the UI agent, the watchdog relaunches it within about a second (up to 20 relaunches a
minute). Every relaunch after the first is logged as `ui_agent.restarted`.

## Install

Requirements:
- Windows 10 or 11 with Windows PowerShell 5.1, which is built in.
- An administrator account for the parent to install with.
- An account for the child. Which kind decides the PC's **mode**, chosen in the download dialog:
  - **Managed** (recommended): the child has a **standard** account and can't switch Kidputer
    Control off.
  - **Trusted**: the child's account is an **administrator**. The schedule still applies day to
    day, but the child *could* switch it off, so the safety net is notifications (installs, and the
    PC going silent).

**One-click installer (recommended):**
1. In the dashboard, click **Add a PC** (or open the device's page) and click **Download
   installer**. You can type the child's Windows account name first, or leave it blank and pick it
   during install.
2. Copy the zip to the child's PC and unzip it.
3. Signed in as a parent (administrator), double-click **Install Kidputer Control**. Click **Yes**
   when Windows asks for permission.
   - If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
4. Pick the child's account from the numbered list. In Managed mode the list shows only the PC's
   standard (non-administrator) accounts; in Trusted mode it includes administrators.
5. Set the schedule and lock-screen PIN in the dashboard. The PC picks them up within one poll
   interval, 30 seconds by default.

What's in the download:
- `Install.ps1`, `Uninstall.ps1`, and the `KidputerControl` folder, the same files as in this
  repository.
- `install-config.json`: the server address and a one-time enrollment code, valid for 24 hours.
- `Install Kidputer Control.cmd`: asks Windows for administrator permission, then runs
  `Install.ps1`.

Each download issues a new code and cancels any earlier one. The code isn't copied onto the PC's
installed files.

**By hand:** if you'd rather not use the download, click **Install by hand instead** under the
download button to get an enrollment code. Then, in an administrator PowerShell in this `agent`
folder, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\Install.ps1 -ApiUrl https://kidputer-control.onrender.com -EnrollmentCode ABCD-EFGH-JKMN -ChildUser Sam
```

Leave out `-ChildUser` to pick the account from a list. For a Microsoft account, use
`-ChildUser MicrosoftAccount\sam@example.com`. Add `-Mode trusted` if the child's account is an
administrator (Managed mode refuses an administrator account).

To switch mode later, change the account type in Windows Settings and flip the mode on the
device's page in the dashboard. No reinstall is needed.

To upgrade, run `.\Install.ps1` again with no arguments. It keeps the existing enrollment. To move
the PC to a new device record, pass a new `-EnrollmentCode`.

To uninstall, run `.\Uninstall.ps1` as administrator (add `-KeepLogs` to keep the local logs).
Then delete the device in the dashboard.

## What goes where

| Path | ACL | Contents |
|---|---|---|
| `%ProgramFiles%\KidputerControl\` | Users: read/execute | The scripts. Their SHA-256 hashes are checked every minute. |
| `%ProgramData%\KidputerControl\config\` | SYSTEM + Administrators | `agent.json`, `device.token` (DPAPI), `state.json` (cached config, including the PIN hash), `local.json`, `queue.jsonl` (events not yet uploaded), `manifest.json`, `scans\`, `security\` (Security tab: what's been seen and the last job results) |
| `%ProgramData%\KidputerControl\logs\` | SYSTEM + Administrators | `agent-YYYY-MM.log`, one file per month, 12 months kept |
| `%ProgramData%\KidputerControl\ipc\ui-state.json` | Users: read | What the UI should show. Written only by the watchdog. |
| `%ProgramData%\KidputerControl\ipc\inbox\` | Users: create files only | Requests from the UI agent. The watchdog validates each one, then deletes it. |

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

1. **Task:** `Get-ScheduledTask -TaskPath \KidputerControl\` shows `Watchdog` as *Running*.
   `%ProgramData%\KidputerControl\logs\agent-*.log` shows `agent.started`, with no loop errors.
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
10. **Tamper:** as an administrator, edit a file under `%ProgramFiles%\KidputerControl`. Within a
    minute a critical `tamper.detected` event appears. Re-run `Install.ps1` to restore the files.
11. **Parent account:** sign in as the parent. No countdown or lock screen appears.
12. **Shutdown and silence:** shut the PC down normally. The dashboard log shows "PC shutting
    down", and 15 minutes later "PC turned off" rather than a "PC went silent" alert. Turn it back
    on: "PC back online" appears. (If "PC went silent" appears instead, the shutdown signal didn't
    reach the server in time; that's a false alarm, not a fault.)
13. **Sleep:** put the PC to sleep for a few minutes and wake it. The log shows "PC woke from
    sleep".
14. **Running apps:** with the child signed in and an app open (Notepad doesn't count: Windows'
    own programs are hidden), open the device's **Apps** tab. The app appears within a few
    seconds, with its name and memory use.
15. **Closing an app:** click **Close**, then **Warn, then close in 1 minute**. The child sees
    "<app> closes in 1:00" bottom-right. When it reaches zero the app is asked to close (it can
    still offer to save) and is force-closed 5 seconds later if it's still open. The dashboard
    shows "Closed". Try **Close right away** too.
16. **Auto-update:** after deploying a server with changed agent files, the PC updates at its
    next check-in (a failed attempt is retried after an hour). The log shows "Kidputer Control updated from ... to ...",
    the device page shows the new agent version, and no tamper alert follows. Turning
    auto-update off on the device page stops this.
17. **Change monitoring** (after the first two days, which only learn what's normal):
    - Install a small program (machine-wide, and one just for the child): "App installed"
      appears within a minute; uninstall it: "App removed".
    - Unzip and run a portable program, or run one straight from a zip: "New program run",
      saying it came from an archive.
    - Save a .zip into the child's Downloads: "New archive file" within 5 minutes.
    - Plug in a USB stick: "USB drive connected", with a count of programs and archives on it.
    - Create a Windows account, or make one an administrator: an account alert within 2 minutes.
    - Turn off Defender's real-time protection: "Virus protection switched off" within 5 minutes.
18. **App rules:** on the App rules tab, block Notepad++ or another non-Windows app (add it under
    "An app that isn't listed..."). Opening it closes it within about a second, and a box says
    it isn't allowed. Then give a category a schedule that ends in a couple of minutes, with one of
    its apps open: when the time ends, a countdown appears before it closes. Rename a blocked
    program's .exe: it's still recognised.
19. **Usage:** use a couple of apps for a few minutes each, leave the PC idle for 5 minutes, then
    check the Usage tab (it updates every 5 minutes): the apps appear, and the idle time isn't
    counted.
20. **Files:** on the Files tab, click **Scan for folder sizes** on C:. Task Manager shows a
    low-priority PowerShell process for a few minutes; games stay smooth. When it's done, folder
    sizes appear; click into Users and use **Show what's in this folder now**. Plug in a USB stick:
    it appears in the drive list.
21. **Virus protection:** open the Security tab: Defender shows as on, with recent definitions.
    Click **Quick scan**: the tab says it's running, and "Virus scan finished" appears in the log
    within about 15 minutes. Download the harmless EICAR test file
    (<https://www.eicar.org/download-anti-malware-testfile/>): a critical "Threat found" alert
    arrives, and the threat is listed as quarantined or removed.
22. **Windows updates:** the tab shows the Windows version, and within a few minutes of the agent
    starting, when updates were last checked. Click **Check now**, then **Install now** if anything
    is waiting: "Windows updates installed" appears in the log. On Windows Pro, the "Pause
    updates" button in Windows Settings is gone within 30 minutes (and the tab shows "Pausing
    updates: Blocked"); on Home, pause updates there: a "Windows updates paused" alert arrives
    within 30 minutes. On a laptop on a phone hotspot set as metered, waiting updates show "Waiting
    to install until the PC is off a metered connection".
23. **Restart at bedtime:** with an update waiting for a restart, set the bedtime lock a few
    minutes away. The countdown says the PC restarts tonight; 20 minutes after it locks, the PC
    restarts (with a 2-minute Windows notice), and the log shows "Restarting for updates".

## Tests

```powershell
.\tests\Parse.Tests.ps1     # every script parses; the Win32 interop compiles
.\tests\Common.Tests.ps1    # decision logic, countdown, PIN and lockout, queue, inbox, tamper, install modes
.\tests\Apps.Tests.ps1      # running apps: which processes may be closed, the process report
.\tests\Update.Tests.ps1    # auto-update: fingerprints, install, rollback
.\tests\Monitor.Tests.ps1   # change monitoring: installs, first runs, archives, accounts, USB, VPNs
.\tests\Files.Tests.ps1     # drives, folder listing, when scans are due
.\tests\Scanner.Tests.ps1   # the folder-size scanner and Scan.ps1
```

Both run on Windows PowerShell 5.1 and on PowerShell 7 (Windows CI runs both).
`Integration.Tests.ps1` runs the agent's HTTP code against a live API. It is driven by
`server/tests/test_agent_integration.py`.
