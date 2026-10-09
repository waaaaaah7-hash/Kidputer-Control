# Kidputer Control — Roadmap

This is the plan after version 1.0.0. It includes the features already on record (app and process
management, usage quotas, weekly reports, bonus time), the Managed and Trusted modes with change
notifications, and further suggestions. Each one is rated for effort, feasibility, and
practicality.

**Effort:**
- **S:** a small, contained change.
- **M:** touches the agent, server, and dashboard.
- **L:** a substantial new subsystem.

## Summary

| # | Feature | Source | Effort | Feasibility | Practicality |
|---|---|---|---|---|---|
| 0 | Verify 1.0 on a real Windows PC | Prerequisite | S (parent's time + fixes) | High | Essential |
| 1 | See running apps and processes | Requested | M | High | High |
| 2 | Stop a running app remotely | Requested | M | High | High |
| 3 | Schedule availability of common apps | Requested | L | High, with caveats | High |
| 4 | Add custom apps | Requested | S (on top of #3) | High | High |
| 5 | Per-app usage time | Suggested (feeds #6–7) | M | High | High |
| 6 | Daily screen-time quotas | Original roadmap | M | High | High |
| 7 | Weekly reports (email) | Original roadmap | M | High | Medium |
| 8 | Bonus time (time bank) | Original roadmap | S | High | Low: extra time already covers most of it |
| 9 | "Ask for more time" button on the lock screen | Suggested | M | High | High |
| 10 | Parent notifications (email and push), including "PC went silent" | Requested | M | High | Essential in Trusted mode |
| 11 | Schedule templates and holidays | Suggested | S | High | Medium |
| 12 | One-click installer download from the dashboard **(done)** | Suggested | S | High | Medium |
| 13 | Agent auto-update | Suggested | M | Medium | High, once #1–4 ship |
| 14 | Several children on one PC | Suggested | M | High | Depends on the household |
| 15 | Web/site filtering | Often expected | L | Low | Better handled outside the app |
| 16 | Managed and Trusted modes, chosen in the installer | Requested | S | High | High |
| 17 | Change monitoring: installs, first-run programs, archives, accounts, and more | Requested | M–L | High (archive "access": medium) | High |
| 18 | Install requests approved by a parent (Managed mode) | Requested | L | High for silent installs | High |
| 19 | Drives, file explorer, and folder-size analysis (like TreeSize) | Requested | L | High | High |
| 20 | Passkeys: sign in with Face ID, fingerprint, or Windows Hello | Requested | M | High | High |
| 21 | Remember this device, with a signed-in devices page and re-checks for risky actions | Requested | S | High | High |

## Release plan

| Version | Contents |
|---|---|
| **1.0.x** | Verify on a real PC (#0) and fix whatever turns up. One-click installer (#12, done). |
| **1.1** | Managed and Trusted modes (#16). Parent notifications, with "PC went silent" and tamper alerts (#10). See and stop running apps (#1, #2). Agent auto-update (#13). Passkeys and remember this device (#20, #21). |
| **1.2** | Change monitoring (#17), built on the same process monitoring as #1. Drives, file explorer, and folder-size analysis (#19). App availability schedules, the app catalogue, and custom apps (#3, #4). Per-app usage time (#5). |
| **1.3** | Install requests for Managed mode (#18). Daily quotas (#6), "ask for more time" (#9), schedule templates and holidays (#11). |
| **Later** | Weekly reports (#7), time bank (#8), several children per PC (#14), as needed. |

#20 and #21 change only the server and dashboard, not the PC agent, so they can ship ahead of the
rest of 1.1 if wanted. #10 together with #16 is the most valuable early step whichever mode is chosen, so it moves up to
1.1. #17 shares the process-start monitoring with #1, and #18 carries the most security risk, so it
comes last.

---

## 0. Verify 1.0 on a real Windows PC

None of the Windows-side code has yet run on an actual Windows PC: the lock-screen windows, the
launch into the child's session, the scheduled task, and the folder permissions. App control sits
on top of all of these, so run the first-run checklist in `agent/README.md` once before building
more on them.

## 1. See running apps and processes — M

- The SYSTEM watchdog lists the processes in the child's session: name, path, start time, memory,
  and whether the process has a visible window.
- The dashboard shows two groups:
  - **Apps:** programs with a window, such as Minecraft or Chrome.
  - **Background** processes.
- Windows' own system processes are hidden by default.
- The list refreshes when the parent opens the page, and every few seconds while it stays open.
- **Privacy:** window titles can reveal browser tabs or document names, so showing them is an
  opt-in setting.

## 2. Stop a running app remotely — M

- Each app in the list gets a **Close** button.
- The PC picks up the request on its next check. While a stop is pending, that check happens about
  every 5 seconds instead of every 30.
- **Gentle by default:** the child first sees a warning, for example "Minecraft will close in 60
  seconds — save your game", in the same style as the bedtime countdown. The app is forced closed
  only if it's still running when the countdown ends. This keeps the 1.0 principle that nothing
  loses work without warning. "Close immediately" stays available as an option.
- **Protected list:** Windows itself, Explorer, and Kidputer Control's own processes can never be
  stopped from the dashboard.
- Every stop is recorded in the activity log.

## 3. Schedule availability of common apps — L

**Rules**
- Rules apply to an app or a category, per weekday.
- For example: "Games: weekdays 16:00–19:00, weekends 10:00–20:00".
- A rule can also block an app entirely or allow it at any time.

**App catalogue** (kept on the server, so it can be updated without reinstalling anything on the
PC):

| Category | Apps |
|---|---|
| Games | Minecraft (Java and Bedrock), Roblox, Fortnite and the Epic Games Launcher, all Steam games, the Xbox app |
| Browsers | Chrome, Edge, Firefox, Brave, Opera |
| Chat and social | Discord, TikTok, WhatsApp |
| Video and music | Netflix, Spotify |

**Enforcement**
- The watchdog subscribes to Windows' process-start events, so it hears the moment any program
  starts.
- It closes a blocked app within about a second and shows a message such as "Not allowed right
  now — available at 16:00".
- Windows' preventive app blocking (AppLocker) isn't available on Windows Home, and the other
  preventive options are fragile. So the app does flash open briefly before it's closed.

**Recognising renamed apps**
- A child can copy a game and rename it, for example `Minecraft.exe` → `homework.exe`.
- Rules therefore match on details inside the program file that survive a rename: its original
  file name, product name, and publisher.
- For Minecraft Java, which runs on a generic Java runtime, rules match on the command line.

**Offline**
- Rules are cached on the PC and keep working without a connection, the same as the bedtime
  schedule.

**Known limits**
- Browser-based games and web versions of apps can't be stopped by app rules (see #15).
- Portable apps on a USB stick are caught only if they're in the catalogue or match a rule.
- A determined, technical teenager will find gaps. The aim is to stop casual use reliably, not
  every possible attempt.

## 4. Add custom apps — S (once #3 exists)

- **Main route:** pick from **"Apps seen on this PC"**, built from the process list in #1. The
  parent clicks "Add rule" next to something the child has been running; no file paths needed.
- **Fallback:** enter an exe name or path by hand.
- Custom apps can go into the parent's own categories, for example "Homework apps".

## 5. Per-app usage time — M

- A natural by-product of #1 and #3. It's also the data that makes #6 and #7 worthwhile.
- The UI agent runs in the child's session, so it can tell which app is in front and whether the
  child is idle. Time spent with nothing touched isn't counted.

## 6. Daily screen-time quotas — M

- For example: "2 hours of games per day" or "3 hours of screen time per day".
- The child gets the countdown warning as time runs out.
- Usage is counted on the PC and synced to the server, so quotas work offline.
- Fits naturally into the #3 rules, for example "Games: 2 h/day within 16:00–20:00".

## 7. Weekly reports — M

- An email summary covering:
  - hours per day
  - top apps
  - blocked attempts
  - tamper alerts
- Needs an email provider such as Postmark, Resend, or the parent's own SMTP, because Render
  doesn't send email.
- Only worth building after #5 exists.

## 8. Bonus time (time bank) — S

- The dashboard's "+30 min" extra-time buttons already cover most of this.
- What's missing is a **time bank**: minutes the parent awards in advance, which the child can
  spend whenever they choose.
- Nice to have, so it's last in the plan.

## 9. "Ask for more time" — M

- A button on the countdown and on the lock screen.
- The parent gets a notification and approves or declines it from their phone.
- It cuts down on negotiation and gives the child a sanctioned way to ask.
- Depends on #10.

## 10. Parent notifications — M

**Channels**
- **Email** through a provider such as Postmark or Resend. Free tiers usually cover a household.
- **Phone push notifications**, with the dashboard installed as a phone app (a PWA). This works on
  Android, and on iPhone once the app is added to the home screen.

**Controls**
- Each event type can be set to **immediate**, **daily digest**, or **off**.
- Quiet hours.
- Repeats are grouped, so extracting 50 zip files sends one alert, not 50.

**"PC went silent"**
- The server raises an alert such as "No contact from Sam's PC for 15 minutes while it should be
  on".
- An administrator account can stop the agent before it reports anything. This server-side check
  is the one signal that can't be suppressed from the PC, so it's essential in Trusted mode (#16).

**Events covered**
- Tamper alerts and the PC going silent.
- Time requests (#9) and blocked-app attempts (#3).
- Everything detected by change monitoring (#17).

## 11. Schedule templates and holidays — S

- Reusable templates, such as "School nights" and "Weekend".
- Date ranges that switch templates, for example "Summer holiday: use the weekend schedule every
  day".

## 12. One-click installer download — S — done

- A zip downloaded from the dashboard, with the server address and enrollment code already filled
  in.
- The parent no longer has to type the install command.
- Built: double-click the launcher, approve the Windows permission prompt, and pick the child's
  account from a list. See `agent/README.md`.

## 13. Agent auto-update — M

- Today, every agent change means re-running the installer on the PC.
- App control will change more often than bedtime enforcement does, so this becomes important
  after 1.1.
- Each update must be checked against a fingerprint from the server before it's applied, so
  updates can't be used to slip in unwanted code.

## 14. Several children on one PC — M

- 1.0 enforces one child account per PC.
- Siblings sharing a family PC would each need their own schedule and app rules.

## 15. Web/site filtering — not recommended

- Filtering websites properly means intercepting browser traffic, which is a large and fragile
  project.
- A family-safe DNS service set on the home router covers every device in the house instead.
  Examples are Cloudflare for Families (free) and NextDNS.
- The practical route is a setup guide in the docs rather than a feature.

## 16. Managed and Trusted modes — S

The installer, and the dashboard's download dialog, ask which mode the PC uses.

| | **Managed** | **Trusted** |
|---|---|---|
| Child's account | Standard | Administrator |
| Schedule, lock now, app rules | Enforced, and the child can't switch them off | Enforced day to day, but the child *could* switch them off |
| Installing apps | Store and per-user apps freely; anything else through install requests (#18) | The child installs anything; the parent is notified (#10, #17) |
| Main safety net | Enforcement | Notifications, especially "PC went silent" |

- In Trusted mode, the installer stops refusing an administrator child account.
- The dashboard records each PC's mode, and labels Trusted PCs "can be switched off by the child".
- The agent already reports whether the child's account is an administrator, so the dashboard warns
  if the mode doesn't match the account type.
- Switching later: change the account type in Windows' Settings and flip the mode in the
  dashboard. No reinstall needed.

**What an administrator account can do in Trusted mode, and how the parent would know**

| The child could… | Signal |
|---|---|
| Disable or delete the watchdog's scheduled task | PC went silent (#10) |
| Stop the watchdog in an elevated Task Manager | PC went silent, unless it restarts within a minute |
| Uninstall, or delete the program folder | "Agent uninstalled" or tamper event if sent first; otherwise PC went silent |
| Block the server in the firewall and edit the cached settings | PC went silent |
| Change the clock | Clock warning, but only while online |
| Create a second, unmonitored Windows account | New-account alert (#17) |

## 17. Change monitoring — M–L

These events feed the notifications in #10. Most are useful in both modes; some matter mainly in
Trusted mode.

| Event | How it's detected | Feasibility | Practicality |
|---|---|---|---|
| **App installed or uninstalled** | Compare Windows' installed-programs list (machine-wide and the child's own) every minute, plus Store apps | High: catches standard installers; Steam games register there too | High |
| **New program run for the first time** | Process-start events (shared with #1): "First run of `game.exe` from Downloads" | High | Very high: catches portable games and tools that never "install", which is usually what a zip turns out to contain |
| **Compressed files (.zip, .rar, .7z)** | Three signals, listed below | Medium | Medium |
| **New Windows account created, or someone added to Administrators** | Local accounts and group membership | High | High: a second account wouldn't be monitored |
| **Kidputer Control tampered with** (task disabled, files changed, uninstalled, clock changed) | Already detected in 1.0; this adds notifications | High | Essential |
| **Windows Defender protection turned off** | Windows' Defender status | High | Medium, mainly Trusted mode |
| **USB drive connected** | Windows device events | High | Medium: a common way to bring in portable games |
| **VPN or remote-access app installed** (NordVPN, AnyDesk, TeamViewer) | A flag in the app catalogue (#3) on top of install detection | High | Medium-high: VPNs are the usual way around router-level web filtering |

**Compressed files.** Three signals:
- New archive files appearing in Downloads, Desktop, or Documents.
- Archive tools (7-Zip, WinRAR) opening one.
- Zips opened in Explorer, seen through Windows' Recent Items list. The child can turn that list
  off.

Strict detection of an archive being *accessed* isn't practical. Opening a zip in Explorer happens
inside Explorer itself and leaves no clean trace, and Windows' file-access auditing is noisy and
heavy. So archives are **digest** items by default, and "a program from a zip was run" (via
first-run detection) is the **immediate** alert.

**Deliberately excluded:** keystroke logging, screenshots, and browser history. They cost far more
trust than they're worth with a teenager. Telling the child what's monitored is recommended.

## 18. Install requests (Managed mode) — L

**Requesting**
- Windows' own administrator prompt runs on a protected screen that other programs can't
  intercept, so the child asks through Kidputer Control instead.
- A "Request an app install" shortcut opens a file picker, and the child chooses the installer.
- The watchdog copies it into a protected folder, so it can't be swapped after approval.
- The parent sees:
  - the file name and size
  - the publisher, and whether its digital signature is valid
  - a fingerprint, with a link to look it up on VirusTotal

**Running approved installs**

Any installer window running with administrator rights on the child's screen is an escape hatch:
its "Browse…" dialog can open a command prompt with full rights. Approved installs therefore run
silently, without a window:

| Installer type | Approach | Feasibility |
|---|---|---|
| MSI packages and common installer builders (Inno Setup, NSIS) | Run silently, without a window | High: standard silent-install switches |
| Apps in the winget catalogue (Discord, Steam, OBS, VLC, and thousands more) | The child requests by name; the parent approves; installed silently | Medium-high: winget is a little temperamental when run by a system service, but it works |
| Anything else (custom installers with a window) | "Install it in person": the parent types their password at Windows' own prompt | High; less convenient |

Every request and approval is recorded in the activity log.

## 19. Drives, file explorer, and folder-size analysis — L

A **Files** tab on the device page. It shows **names, sizes, and dates only**: opening, previewing,
or downloading files is deliberately out of scope, for privacy, and because it would turn the
dashboard into a way to pull any file off the PC.

**What the parent sees**
- **Drives:** every drive (internal, USB stick, external disk, SD card, network drive) with size and
  free space, plus connected devices such as phones, by name.
- **Explorer:** click through folders. Each folder shows its **total size**, its file count, and
  when it last changed. Folders sort by size, with a bar for each one's share of its parent, as in
  TreeSize.
- **Largest files:** the 100 biggest files on the drive.
- **By type:** space used by videos, games and programs, archives, pictures, and so on.
- **What changed** since the previous scan, for example "Downloads grew 12 GB since last Sunday".
  This also feeds change monitoring (#17).
- **USB alert with a summary**, for example "USB drive 'SANDISK 32GB' connected: 3 programs, 2 zip
  files, 1.2 GB". This builds on #17.

**How it works**

Scanning:
- Folder totals require scanning every file, because Windows doesn't store a folder's total size.
- PowerShell's built-in file listing could take 10+ minutes for a whole drive. A small C# helper
  using Windows' fast file-listing calls (loaded with `Add-Type`, like the session launcher) should
  take roughly one to a few minutes.
- Reading the drive's internal file table directly, as TreeSize does, is faster but much more
  complex. It's only worth doing if scans prove too slow.
- Scans run as a separate background process at low priority, so games don't stutter and the
  watchdog keeps enforcing. The dashboard shows progress.
- Scans run on demand ("Scan now") and on a schedule, for example nightly or weekly while the PC is
  idle. The latest results stay viewable while the PC is off.

Accuracy (the same pitfalls TreeSize handles):
- Folder links (junctions) aren't followed, so nothing is counted twice and nothing loops.
- OneDrive online-only files are marked as not using disk space.
- Both "size" and "size on disk" are recorded.
- Unreadable system folders show as "not accessible" rather than silently missing.

Upload and browsing:
- Each scan sends the folder tree with totals, plus the largest files: a few hundred KB compressed,
  not a list of every file.
- Opening a folder fetches its files live. A "live session" makes the PC check in every second or
  two while the parent is browsing, shared with #2.

Scope and limits:
- Whole drives are covered, including Program Files and Steam libraries where games live.
- Encrypted (BitLocker) USB sticks can't be read until they're unlocked.
- Phones connected by USB are listed by name only. Browsing their contents is unreliable through
  Windows, especially for iPhones, so it's not planned.

| Part | Effort | Feasibility | Practicality |
|---|---|---|---|
| Drive and device list, USB alert with a summary | S | High | High |
| File explorer (live folder browsing; names, sizes, dates) | M | High | High |
| Fast scanning engine (C#, background, low priority, scheduled) | M | High | High |
| Size view (sortable tree, largest files, by type, what changed) | M | High | High: finding big games, stashed downloads, or a full disk |

## 20. Passkeys — M

Sign in to the dashboard with Face ID, a fingerprint, or Windows Hello instead of a password and
6-digit code. Passkeys are built into phones and browsers (iCloud Keychain, Google Password Manager,
Windows Hello, and password managers such as 1Password).

**Why a passkey counts as multi-factor on its own**
- **Something you have:** a cryptographic key stored on the phone or computer. It never leaves the
  device and can't be typed into a fake website.
- **Something you are or know:** using the key requires the fingerprint, face, or device PIN.
- NIST treats passkeys used this way as multi-factor, including passkeys that sync between devices.

**Requirements**
- **User verification must be required.** The server must reject any passkey sign-in that skipped
  the fingerprint, face, or PIN step. Without it, a passkey is only a single factor.
- **The password plus authenticator code stays as a backup**, for a lost phone or a switch between
  phone ecosystems.
- Several passkeys per account (for example, phone and laptop), each named and removable from the
  Account page.

**Benefits over the current sign-in**
- It can't be phished. A fake site can capture a 6-digit code and replay it within its 30-second
  window, but a passkey only works on the real site's address.
- Faster: one tap and a glance or touch.

**Caveat:** synced passkeys are only as secure as the Apple or Google account that syncs them, so
that account should have its own 2FA turned on.

**Implementation:** WebAuthn, the standard behind passkeys, via a maintained library (for example
`webauthn` for Python) and the browser's built-in passkey support. It needs a new table for
registered passkeys and two sign-in endpoints (start and finish).

## 21. Remember this device — S

Today a sign-in lasts a fixed 12 hours, and the authenticator code is asked at every sign-in.

**Remember this device**
- A "Remember this device for 30 days" checkbox at sign-in.
- On a remembered phone or browser, the authenticator code is skipped. The password is still
  needed when a session ends, unless passkeys (#20) are used.
- Optionally, sessions extend while in use instead of ending after a fixed 12 hours.

**Safeguards, built as part of this item**

The dashboard can unlock the child's PC, so anyone holding the parent's unlocked phone could do so
too. Two safeguards keep long sign-ins safe:
- **Signed-in devices page:** every active sign-in and remembered device, with browser, approximate
  location (from the IP address), and last use. Any of them can be signed out, for example a lost
  phone.
- **Re-check for risky actions:** even on a remembered device, ask for Face ID / fingerprint (#20) or
  the authenticator code before deleting a PC, changing the lock-screen PIN, downloading an
  installer, or approving an install request (#18).

**Implementation:** a long-lived, revocable "remembered device" token (stored only as a hash, like
sessions today), plus a recent-verification timestamp on each session for the re-checks.
