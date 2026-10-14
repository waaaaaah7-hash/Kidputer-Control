# Instructional videos

Nine narrated videos, recorded from the real dashboard with a simulated child's PC. Steps that
can't be filmed here (Render's website, Windows on the child's PC) are shown as slides.

| Video | For | What it shows |
|---|---|---|
| `01-tour` | Everyone | Home page, a PC's page, lock now, extra time, messages, closing an app, activity, notifications |
| `02-render-setup` | Whoever sets it up | Creating the service on Render, first sign-in with two-factor, tidying up, email alerts |
| `03-install-on-pc` | Whoever sets it up | Adding a PC, Managed and Trusted modes, the installer, the schedule and PIN |
| `04-from-your-phone` | Parents | Home screen install, everyday actions on a phone, phone notifications |
| `05-rules-usage-files` | Parents | App rules, usage time, the Files tab, change alerts |
| `06-parents-and-security` | Parents | Inviting the other parent, passkeys, signed-in devices, built-in protections |
| `07-first-run-checklist` | Whoever sets it up | The one-time check on a real Windows PC (`agent/README.md`) |
| `08-for-developers` | Developers | How the project is built, branches, tests, traps |
| `09-security` | Parents | The Security tab: threats, scans, Windows updates, restarts at bedtime, settings, alerts |

The videos themselves aren't kept in the repository (they're large and change with the design).
Re-record them after the dashboard changes.

## Re-recording

The script is `server/tests/test_videos.py`, which uses `server/tests/videokit.py`. It's a
test file so it can reuse the browser-test setup, and it's skipped unless `KC_VIDEOS_DIR` is set.

1. Have what the browser tests need: the test Postgres, `pip install playwright`, and a built
   dashboard (`cd web && npm ci && npm run build`).
2. Install the narration and video tools: `pip install kokoro-onnx soundfile imageio-ffmpeg`.
3. Download the Kokoro voice model (Apache 2.0), `kokoro-v1.0.int8.onnx` as `kokoro.onnx` and
   `voices-v1.0.bin` as `voices.bin`, into `~/.cache/kokoro` (or another folder named by
   `KC_VIDEO_MODEL_DIR`), from
   <https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0>.
4. Record (about 35 minutes for all nine; they play in real time):

   ```bash
   cd server
   KC_VIDEOS_DIR=../videos pytest tests/test_videos.py
   KC_VIDEOS_DIR=../videos pytest tests/test_videos.py -k tour   # just one
   ```

Each video comes out as an `.mp4` (H.264 and AAC, so it plays everywhere). The narration is
generated offline, so no text leaves the computer; clips are cached in `.narration-cache`, so
re-recording after a small change is quicker. The moments spent generating the voice are cut out of
the video automatically. `KC_VIDEO_VOICE` picks another voice (default
`bf_emma`, British English).

## Changing a video

Each video is one test function: a sequence of `with r.say("..."):` blocks, where the narration
plays while the actions inside run, and the next step waits until it has finished. `r.click`,
`r.type` and `r.point` move a visible pointer first. `r.steps(...)` shows a slide whose lines appear
one at a time, each with its own narration. `FakePC` stands in for the agent: it checks in, applies
locks and messages, reports running apps, and answers Close and folder requests.
