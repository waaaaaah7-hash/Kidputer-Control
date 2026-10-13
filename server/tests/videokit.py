"""Tools for recording the instructional videos (see test_videos.py).

A Recorder drives the real dashboard in Chromium while Playwright records the screen. It adds what a
screen recording lacks: a visible pointer that moves to whatever is clicked, captions, title cards,
and narration. Narration is generated offline with the Kokoro voice model, so no text is sent
anywhere. Steps that can't be filmed here (Render's website, Windows on the child's PC) are shown
as slides. FakePC plays the part of the child's PC: it checks in, locks, closes apps and answers
folder requests like the real agent would.

Only test_videos.py uses this, and only when KC_VIDEOS_DIR is set. Heavy imports stay inside
functions so the normal test run doesn't need them.
"""

from __future__ import annotations

import base64
import hashlib
import html
import os
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
FONT = REPO / "web" / "node_modules" / "@fontsource-variable" / "figtree" / "files" / "figtree-latin-wght-normal.woff2"
VOICE = os.environ.get("KC_VIDEO_VOICE", "bf_emma")
MODEL_DIR = Path(os.environ.get("KC_VIDEO_MODEL_DIR", Path.home() / ".cache" / "kokoro"))


# --- Narration ------------------------------------------------------------------------------------

class Narrator:
    """Text to speech with Kokoro (kokoro-onnx). Clips are cached by text, so re-recording is quick."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._kokoro = None
        self.rate = 24000

    def speak(self, text: str):
        import numpy as np
        import soundfile as sf

        key = hashlib.sha256(f"{VOICE}|{text}".encode()).hexdigest()[:20]
        path = self.cache_dir / f"{key}.wav"
        if not path.exists():
            if self._kokoro is None:
                from kokoro_onnx import Kokoro

                self._kokoro = Kokoro(str(MODEL_DIR / "kokoro.onnx"), str(MODEL_DIR / "voices.bin"))
            samples, rate = self._kokoro.create(text, voice=VOICE, speed=1.0, lang="en-gb" if VOICE.startswith("b") else "en-us")
            sf.write(path, samples, rate)
        samples, self.rate = sf.read(path, dtype="float32")
        return np.asarray(samples)


# --- In-page overlays (pointer, captions, title cards) -----------------------------------------------
# Built with DOM calls and element.style, which the dashboard's Content-Security-Policy allows.

OVERLAY_JS = """
(opts) => {
  if (document.getElementById('kc-cap')) return;
  const add = (id, css) => { const el = document.createElement('div'); el.id = id; Object.assign(el.style, css); document.documentElement.appendChild(el); return el; };
  const font = "'Figtree Variable', 'Figtree', 'Segoe UI', system-ui, sans-serif";
  add('kc-cap', { position: 'fixed', left: '50%', transform: 'translateX(-50%)', bottom: opts.phone ? '96px' : '26px', maxWidth: opts.phone ? '88%' : '78%',
    padding: opts.phone ? '8px 12px' : '10px 18px', borderRadius: '12px', background: 'rgba(28,22,12,0.86)', color: '#fff', font: (opts.phone ? '600 15px/1.35 ' : '600 20px/1.4 ') + font,
    textAlign: 'center', zIndex: 2147483646, pointerEvents: 'none', opacity: '0', transition: 'opacity 0.25s', boxShadow: '0 6px 24px rgba(0,0,0,0.25)' });
  const cur = add('kc-cursor', { position: 'fixed', left: '50%', top: '60%', width: '22px', height: '22px', marginLeft: '-11px', marginTop: '-11px', borderRadius: '50%',
    background: 'rgba(217,99,30,0.35)', border: '2.5px solid #d9631e', zIndex: 2147483647, pointerEvents: 'none', opacity: '0',
    transition: 'left 0.7s cubic-bezier(.4,0,.2,1), top 0.7s cubic-bezier(.4,0,.2,1), transform 0.15s, opacity 0.3s' });
  const card = add('kc-card', { position: 'fixed', inset: '0', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '14px',
    background: '#fbf8f3', color: '#22201c', font: '400 20px/1.4 ' + font, textAlign: 'center', padding: '24px', zIndex: 2147483645, opacity: '0', pointerEvents: 'none', transition: 'opacity 0.5s' });
  card.innerHTML = '';
}
"""


def _slide_css() -> str:
    font = ""
    if FONT.is_file():
        font = "@font-face { font-family: 'Figtree'; src: url(data:font/woff2;base64,%s) format('woff2'); font-weight: 300 900; }" % base64.b64encode(FONT.read_bytes()).decode()
    return font + """
    * { box-sizing: border-box; }
    html, body { margin: 0; height: 100%; background: #fbf8f3; color: #22201c; font-family: 'Figtree', 'Segoe UI', system-ui, sans-serif; }
    .slide { height: 100%; padding: 56px 72px 110px; display: flex; flex-direction: column; gap: 22px; }
    .phone .slide { padding: 40px 26px 150px; gap: 16px; }
    .eyebrow { font-size: 18px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; color: #1f7a4c; margin: 0; }
    h1 { font-size: 46px; line-height: 1.1; margin: 0; font-weight: 800; letter-spacing: -0.02em; }
    .phone h1 { font-size: 32px; }
    ul.steps { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 14px; }
    ul.steps li { display: flex; gap: 16px; align-items: flex-start; font-size: 26px; line-height: 1.35; opacity: .18; transition: opacity .5s, transform .5s; transform: translateY(6px); }
    .phone ul.steps li { font-size: 19px; gap: 12px; }
    ul.steps li.on { opacity: 1; transform: none; }
    .num { flex: none; width: 42px; height: 42px; border-radius: 50%; background: #1f7a4c; color: #fff; display: grid; place-items: center; font-weight: 800; font-size: 21px; margin-top: -2px; }
    .phone .num { width: 32px; height: 32px; font-size: 16px; }
    .dot { flex: none; width: 14px; height: 14px; border-radius: 50%; background: #1f7a4c; margin-top: 12px; }
    .phone .dot { margin-top: 8px; width: 11px; height: 11px; }
    code, .code { font-family: 'Cascadia Mono', 'DejaVu Sans Mono', ui-monospace, monospace; font-size: .82em; background: #efeae2; padding: 2px 8px; border-radius: 6px; }
    .note { margin-top: auto; font-size: 20px; color: #5a544a; background: #fff; border: 1px solid #ece6db; border-radius: 16px; padding: 14px 20px; }
    .phone .note { font-size: 15px; }
    b, strong { font-weight: 800; }
    .k { display: inline-block; padding: 1px 10px; border-radius: 8px; border: 1.5px solid #1f7a4c; color: #1f7a4c; font-weight: 700; font-size: .9em; }
    .diagram { display: flex; align-items: center; justify-content: center; gap: 22px; margin-top: 24px; flex-wrap: wrap; }
    .box { background: #fff; border: 2px solid #ece6db; border-radius: 20px; padding: 22px 26px; min-width: 220px; text-align: center; font-size: 22px; font-weight: 700; box-shadow: 0 2px 8px rgba(60,40,10,.08); opacity: .18; transition: opacity .5s; }
    .box.on { opacity: 1; }
    .box small { display: block; font-weight: 500; font-size: 17px; color: #5a544a; margin-top: 6px; }
    .box.green { border-color: #1f7a4c; }
    .arrow { font-size: 34px; color: #1f7a4c; font-weight: 800; }
    .title { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; gap: 18px; padding: 40px; }
    .mark { width: 84px; height: 84px; border-radius: 22px; background: #1f7a4c; display: grid; place-items: center; }
    .title h1 { font-size: 56px; }
    .phone .title h1 { font-size: 34px; }
    .title p { font-size: 24px; color: #5a544a; margin: 0; max-width: 820px; }
    .phone .title p { font-size: 18px; }
    """


LOCK_SVG = '<svg width="46" height="46" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>'


# --- Recorder ----------------------------------------------------------------------------------------

class Recorder:
    def __init__(self, browser, name: str, out_dir: Path, narrator: Narrator, base_url: str | None = None, phone: bool = False):
        self.name, self.out_dir, self.narrator, self.phone = name, out_dir, narrator, phone
        self.w, self.h = (390, 844) if phone else (1280, 720)
        scale = 2 if phone else 1
        self.tmp = Path(tempfile.mkdtemp(prefix=f"kc-video-{name}-"))
        self.ctx = browser.new_context(
            base_url=base_url,
            viewport={"width": self.w, "height": self.h},
            device_scale_factor=scale,
            is_mobile=phone,
            has_touch=phone,
            record_video_dir=str(self.tmp),
            record_video_size={"width": self.w * scale, "height": self.h * scale},
        )
        self.page = self.ctx.new_page()
        self.t0 = time.monotonic()
        self.clips: list[tuple[float, Any]] = []
        self.gaps: list[tuple[float, float]] = []
        self.page.on("dialog", lambda d: d.accept())
        self.slide("")

    # Overlays
    def _overlay(self) -> None:
        self.page.evaluate(OVERLAY_JS, {"phone": self.phone})

    def caption(self, text: str | None) -> None:
        self._overlay()
        self.page.evaluate(
            "(t) => { const c = document.getElementById('kc-cap'); if (t) { c.textContent = t; c.style.opacity = '1'; } else { c.style.opacity = '0'; } }", text
        )

    def now(self) -> float:
        return time.monotonic() - self.t0

    @contextmanager
    def say(self, text: str, pause: float = 0.5, caption: bool = True):
        """Narrate `text` while the block runs; afterwards wait until the narration has finished."""
        before = self.now()
        samples = self.narrator.speak(text)
        start = self.now()
        if start - before > 0.15:  # the voice was being generated: cut this still stretch out of the video
            self.gaps.append((before, start))
        self.clips.append((start, samples))
        if caption:
            self.caption(text)
        try:
            yield
        finally:
            left = start + len(samples) / self.narrator.rate + pause - self.now()
            if left > 0:
                time.sleep(left)
            if caption:
                self.caption(None)

    def narrate(self, *texts: str) -> None:
        for t in texts:
            with self.say(t):
                pass

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    # Pointer
    def point(self, locator, settle: float = 0.8) -> None:
        self._overlay()
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        if not box:
            return
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.page.evaluate(
            "([x, y]) => { const c = document.getElementById('kc-cursor'); c.style.opacity = '1'; c.style.left = x + 'px'; c.style.top = y + 'px'; }", [x, y]
        )
        time.sleep(settle)

    def click(self, locator, after: float = 0.5) -> None:
        self.point(locator)
        self.page.evaluate("() => { const c = document.getElementById('kc-cursor'); c.style.transform = 'scale(0.6)'; setTimeout(() => c.style.transform = '', 160); }")
        time.sleep(0.15)
        locator.click()
        time.sleep(after)

    def type(self, locator, text: str, delay: int = 55) -> None:
        self.click(locator, after=0.2)
        locator.press_sequentially(text, delay=delay)
        time.sleep(0.3)

    def scroll_to(self, locator, block: str = "center") -> None:
        locator.evaluate(f"(el) => el.scrollIntoView({{behavior: 'smooth', block: '{block}'}})")
        time.sleep(1.0)

    def scroll_top(self) -> None:
        self.page.evaluate("() => window.scrollTo({top: 0, behavior: 'smooth'})")
        time.sleep(0.8)

    def hide_pointer(self) -> None:
        self._overlay()
        self.page.evaluate("() => { document.getElementById('kc-cursor').style.opacity = '0'; }")

    # Title cards over the live page (used to cover set-up steps too)
    def card(self, title: str, subtitle: str = "") -> None:
        self._overlay()
        self.page.evaluate(
            """([t, s, svg]) => { const c = document.getElementById('kc-card'); c.innerHTML = '';
                 const m = document.createElement('div'); m.innerHTML = svg;
                 Object.assign(m.style, {width: '84px', height: '84px', borderRadius: '22px', background: '#1f7a4c', display: 'grid', placeItems: 'center'});
                 c.append(m);
                 const h = document.createElement('div'); h.textContent = t; Object.assign(h.style, {fontSize: '44px', fontWeight: '800', letterSpacing: '-0.02em', lineHeight: '1.1'});
                 const p = document.createElement('div'); p.textContent = s; Object.assign(p.style, {fontSize: '22px', color: '#5a544a', maxWidth: '780px'});
                 c.append(h, p); c.style.opacity = '1'; }""",
            [title, subtitle, LOCK_SVG],
        )

    def uncard(self) -> None:
        self.page.evaluate("() => { const c = document.getElementById('kc-card'); if (c) c.style.opacity = '0'; }")
        time.sleep(0.6)

    # Slides (static pages, for steps that can't be filmed here)
    def slide(self, body_html: str) -> None:
        # A fresh blank page first: on the dashboard's own address its Content-Security-Policy would block the slide's styles.
        self.page.goto("about:blank")
        self.page.set_content(
            f"<html class='{'phone' if self.phone else ''}'><head><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<style>{_slide_css()}</style></head><body>{body_html}</body></html>"
        )
        self._overlay()
        time.sleep(0.4)

    def reveal(self, index: int) -> None:
        self.page.evaluate("(i) => { const els = document.querySelectorAll('.steps li, .box'); if (els[i]) els[i].classList.add('on'); }", index)

    def steps(self, eyebrow: str, title: str, items: list[tuple[str, str]], numbered: bool = True, note: str = "") -> None:
        """A slide of steps revealed one at a time. items: (text shown, narration)."""
        lis = "".join(
            f"<li><span class='{'num' if numbered else 'dot'}'>{i + 1 if numbered else ''}</span><span>{shown}</span></li>" for i, (shown, _) in enumerate(items)
        )
        note_html = f"<div class='note'>{note}</div>" if note else ""
        self.slide(f"<div class='slide'><p class='eyebrow'>{html.escape(eyebrow)}</p><h1>{html.escape(title)}</h1><ul class='steps'>{lis}</ul>{note_html}</div>")
        for i, (_, spoken) in enumerate(items):
            self.reveal(i)
            with self.say(spoken):
                pass

    def title_slide(self, title: str, subtitle: str) -> None:
        self.slide(f"<div class='title'><div class='mark'>{LOCK_SVG}</div><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>")

    # Finish: stop recording, add the narration, encode an mp4
    def finish(self) -> Path:
        import imageio_ffmpeg
        import numpy as np
        import soundfile as sf

        time.sleep(0.8)
        total = self.now()
        video = self.page.video
        self.ctx.close()
        webm = Path(video.path())
        rate = self.narrator.rate

        def cut(t: float) -> float:
            return t - sum(min(b, t) - a for a, b in self.gaps if a < t)

        keep = "fps=25"
        if self.gaps:
            spans = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in self.gaps)
            keep += f",select='not({spans})',setpts=N/25/TB"
        total = cut(total)
        audio = np.zeros(int((total + 2) * rate), dtype="float32")
        for start, samples in self.clips:
            i = int(cut(start) * rate)
            audio[i : i + len(samples)] += samples[: max(0, len(audio) - i)]
        wav = self.tmp / "narration.wav"
        sf.write(wav, np.clip(audio, -1, 1), rate)
        out = self.out_dir / f"{self.name}.mp4"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(webm), "-i", str(wav), "-map", "0:v", "-map", "1:a", "-vf", keep,
             "-c:v", "libx264", "-preset", "slow", "-crf", "24", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "96k", "-t", f"{total:.2f}", "-movflags", "+faststart", str(out)],
            check=True,
        )
        return out


# --- A pretend child's PC ----------------------------------------------------------------------------

GB = 1024**3


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class FakePC:
    """Plays the agent: checks in, applies locks and messages, reports apps, answers commands."""

    def __init__(self, base_url: str, token: str):
        self.http = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
        self.processes: list[dict[str, Any]] = []
        self.listings: dict[str, list[dict[str, Any]]] = {}
        self.child_signed_in = True
        self._stop = threading.Event()
        self._applied: set[str] = set()
        self._done_cmds: set[str] = set()
        self._thread = threading.Thread(target=self._run, daemon=True)

    @classmethod
    def enroll(cls, base_url: str, code: str, hostname: str = "SAM-PC") -> "FakePC":
        r = httpx.post(f"{base_url}/api/v1/agent/enroll", json={"enrollment_code": code, "hostname": hostname, "agent_version": "1.2.0"})
        r.raise_for_status()
        return cls(base_url, r.json()["device_token"])

    def start(self) -> "FakePC":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def event(self, event_type: str, message: str, severity: str = "info", data: dict | None = None, ago: timedelta = timedelta()) -> None:
        self.http.post("/api/v1/agent/events", json={"events": [{
            "client_event_id": str(uuid.uuid4()), "event_type": event_type, "severity": severity, "message": message,
            "occurred_at": _iso(datetime.now(timezone.utc) - ago), "data": data or {},
        }]})

    def report(self, kind: str, data: dict) -> None:
        self.http.post(f"/api/v1/agent/reports/{kind}", json=data).raise_for_status()

    def _run(self) -> None:
        last_procs = 0.0
        while not self._stop.is_set():
            try:
                self._tick(last_procs)
                if time.monotonic() - last_procs > 2.5:
                    last_procs = time.monotonic()
            except httpx.HTTPError:
                pass
            self._stop.wait(1.2)

    def _tick(self, last_procs: float) -> None:
        state = self.http.get("/api/v1/agent/state").json()
        now = datetime.now(timezone.utc)
        lock = state.get("immediate_lock")
        overrides = [o for o in state.get("overrides", []) if o["starts_at"] <= _iso(now) <= o["ends_at"]]
        status: dict[str, Any] = {"mode": "none", "reason": "none", "ui_agent_alive": True, "console_user_is_child": self.child_signed_in}
        if lock:
            status.update(mode="locked", reason="immediate")
            if lock["id"] not in self._applied:
                self._applied.add(lock["id"])
                self.event("lock.applied", "Immediate lock applied", data={"lock_request_id": lock["id"]})
        elif overrides:
            status.update(reason="override", override_ends_at=max(o["ends_at"] for o in overrides))
        msg = state.get("message")
        if msg and msg["id"] not in self._applied:
            self._applied.add(msg["id"])
            self.event("message.displayed", "Message shown", data={"message_id": msg["id"]})
        self.http.post("/api/v1/agent/heartbeat", json={"agent_version": "1.2.0", "status": status})

        for cmd in state.get("commands", []):
            if cmd["id"] in self._done_cmds:
                continue
            self._done_cmds.add(cmd["id"])
            threading.Thread(target=self._command, args=(cmd,), daemon=True).start()

        live = state.get("live_until")
        if live and live > _iso(now) and time.monotonic() - last_procs > 2.5:
            self.report("processes", {"child_signed_in": self.child_signed_in, "processes": self.processes})

    def _command(self, cmd: dict) -> None:
        kind, payload = cmd["kind"], cmd["payload"]
        if kind == "stop_app":
            time.sleep(min(4, payload.get("grace_seconds", 0)) + 1)
            self.processes = [p for p in self.processes if p.get("path") != payload.get("path")]
            self.event("app.stopped", f"{payload.get('name')} closed")
            result = {"status": "done", "result": {"closed": 1}}
        elif kind == "list_folder":
            time.sleep(1.5)
            path = payload["path"]
            entries = self.listings.get(path.rstrip("\\"), [])
            result = {"status": "done", "result": {"path": path, "entries": entries, "total": len(entries), "truncated": False}}
        else:
            time.sleep(1)
            result = {"status": "done", "result": {}}
        self.http.post(f"/api/v1/agent/commands/{cmd['id']}/result", json=result)


# --- Sample data ---------------------------------------------------------------------------------------

def pick_timezone() -> str:
    """A time zone where it's late afternoon right now, so the bedtime countdown looks natural."""
    from zoneinfo import ZoneInfo

    for tz in ["Europe/London", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles", "Pacific/Honolulu",
               "Asia/Tokyo", "Australia/Sydney", "Asia/Kolkata", "Europe/Berlin", "Asia/Dubai", "America/Sao_Paulo", "Pacific/Auckland"]:
        if 16 <= datetime.now(ZoneInfo(tz)).hour < 18:
            return tz
    for tz in ["Europe/London", "America/New_York", "America/Los_Angeles", "Asia/Tokyo", "Pacific/Honolulu", "Asia/Kolkata"]:
        if 15 <= datetime.now(ZoneInfo(tz)).hour < 20:
            return tz
    return "Europe/London"


APPS = [
    {"pid": 10, "name": "javaw", "path": "C:\\Program Files\\Minecraft\\runtime\\javaw.exe", "has_window": True, "memory_mb": 1800, "description": "Minecraft", "company": "Mojang", "system": False},
    {"pid": 11, "name": "chrome", "path": "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "has_window": True, "memory_mb": 900, "description": "Google Chrome", "company": "Google", "system": False},
    {"pid": 12, "name": "Discord", "path": "C:\\Users\\Sam\\AppData\\Local\\Discord\\Discord.exe", "has_window": True, "memory_mb": 300, "description": "Discord", "company": "Discord Inc.", "system": False},
    {"pid": 13, "name": "Terraria", "path": "D:\\Games\\Terraria\\Terraria.exe", "has_window": True, "memory_mb": 640, "description": "Terraria", "product": "Terraria", "company": "Re-Logic", "system": False},
    {"pid": 14, "name": "OneDrive", "path": "C:\\Users\\Sam\\AppData\\Local\\Microsoft\\OneDrive\\OneDrive.exe", "has_window": False, "memory_mb": 40, "description": "Microsoft OneDrive", "company": "Microsoft", "system": False},
    {"pid": 15, "name": "explorer", "path": "C:\\Windows\\explorer.exe", "has_window": True, "memory_mb": 90, "description": "Windows Explorer", "company": "Microsoft", "system": True},
]


def usage_report(day: str) -> dict:
    return {"days": {day: {
        "_total": {"name": "Screen time", "seconds": 8280},
        "minecraft": {"name": "Minecraft", "category": "games", "seconds": 3900},
        "chrome": {"name": "Google Chrome", "category": "browsers", "seconds": 2280},
        "youtube": {"name": "YouTube", "category": "video", "seconds": 1320},
        "word": {"name": "Microsoft Word", "category": None, "seconds": 780},
    }}}


def drives_report() -> dict:
    return {"drives": [
        {"name": "C:\\", "label": "", "type": "Fixed", "format": "NTFS", "size": 500 * GB, "free": 46 * GB},
        {"name": "E:\\", "label": "SANDISK", "type": "Removable", "format": "FAT32", "size": 32 * GB, "free": 30 * GB},
    ], "devices": ["Sam's iPhone"]}


def scan_report(downloads_gb: int) -> dict:
    def node(name, gb, files, children=None):
        n = {"n": name, "s": int(gb * GB), "a": int(gb * GB), "f": files, "d": len(children or []), "m": 0}
        if children:
            n["c"] = children
        return n

    downloads = node("Downloads", downloads_gb, 140)
    videos = node("Videos", 38, 60)
    sam = node("Sam", downloads_gb + 38 + 9, 4200, [downloads, videos, node("Documents", 9, 3900)])
    users = node("Users", downloads_gb + 38 + 9 + 6, 5000, [sam, node("Public", 6, 800)])
    games = node("Games", 212, 9100, [node("Steam", 180, 7000), node("Minecraft", 32, 2100)])
    windows = node("Windows", 31, 120000)
    pf = node("Program Files", 27, 40000)
    total = users["s"] + games["s"] + windows["s"] + pf["s"]
    return {"root": "C:\\", "tree": {"n": "C:\\", "s": total, "a": total, "f": 178000, "d": 9000, "m": 0, "c": [games, users, windows, pf]},
            "largest": [{"p": "C:\\Games\\Steam\\steamapps\\common\\Big\\data.pak", "s": 41 * GB, "m": 0},
                        {"p": "C:\\Users\\Sam\\Videos\\minecraft-build.mp4", "s": 9 * GB, "m": 0},
                        {"p": "C:\\Users\\Sam\\Downloads\\modpack.zip", "s": 6 * GB, "m": 0}],
            "by_type": {"game data": {"s": 160 * GB, "f": 3000}, "video": {"s": 47 * GB, "f": 80}, "programs": {"s": 30 * GB, "f": 9000},
                        "archives": {"s": 12 * GB, "f": 40}, "pictures": {"s": 4 * GB, "f": 2600}, "documents": {"s": 2 * GB, "f": 1900}},
            "inaccessible": 0}


SAM_LISTING = {
    "C:\\Users\\Sam": [
        {"n": "Documents", "dir": True, "m": "2026-10-12T19:02:00Z", "h": False, "l": False},
        {"n": "Downloads", "dir": True, "m": "2026-10-13T15:40:00Z", "h": False, "l": False},
        {"n": "Videos", "dir": True, "m": "2026-10-11T18:20:00Z", "h": False, "l": False},
        {"n": "NTUSER.DAT", "dir": False, "s": 3 * 1024 * 1024, "m": "2026-10-13T07:01:00Z", "h": True, "l": False},
    ],
    "C:\\Users\\Sam\\Downloads": [
        {"n": "modpack.zip", "dir": False, "s": 6 * GB, "m": "2026-10-13T15:40:00Z", "h": False, "l": False},
        {"n": "OptiFine_1.21.jar", "dir": False, "s": 7 * 1024 * 1024, "m": "2026-10-12T17:15:00Z", "h": False, "l": False},
        {"n": "school-project.docx", "dir": False, "s": 840 * 1024, "m": "2026-10-10T16:05:00Z", "h": False, "l": False},
    ],
}
