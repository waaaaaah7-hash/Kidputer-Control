# Kidputer Control — Roadmap

This is the plan after version 1.0.0. It includes the features already on record (app and process
management, usage quotas, weekly reports, bonus time) and further suggestions. Each one is rated
for effort, feasibility, and practicality.

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
| 10 | Parent notifications (email or push) | Suggested | M | High | High |
| 11 | Schedule templates and holidays | Suggested | S | High | Medium |
| 12 | One-click installer download from the dashboard **(done)** | Suggested | S | High | Medium |
| 13 | Agent auto-update | Suggested | M | Medium | High, once #1–4 ship |
| 14 | Several children on one PC | Suggested | M | High | Depends on the household |
| 15 | Web/site filtering | Often expected | L | Low | Better handled outside the app |

## Release plan

| Version | Contents |
|---|---|
| **1.0.x** | Verify on a real PC (#0) and fix whatever turns up. One-click installer (#12, done). |
| **1.1** | See and stop running apps (#1, #2). Agent auto-update (#13), so later releases are easy to roll out. |
| **1.2** | App availability schedules, the app catalogue, and custom apps (#3, #4). Per-app usage time (#5). |
| **1.3** | Daily quotas (#6), parent notifications (#10), "ask for more time" (#9), schedule templates and holidays (#11). |
| **Later** | Weekly reports (#7), time bank (#8), several children per PC (#14), as needed. |

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

- Covers tamper alerts, a PC going offline, blocked-app attempts, and time requests.
- Email is the simplest to build.
- Phone push notifications would need the dashboard to be installable as a phone app (a PWA);
  that is also doable.

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
