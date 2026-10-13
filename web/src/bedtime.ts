import type { Device, Override, ScheduleEntry } from "./api";
import { deviceStatus, formatTime } from "./format";
import { formatDuration } from "./rules";

/** Minutes in a day and in a week. Schedule days run Monday (0) to Sunday (6). */
const DAY = 24 * 60;
const WEEK = 7 * DAY;

/** A moment as the PC's clock shows it: the local date, weekday (0 = Monday) and minutes since midnight. */
export interface LocalTime {
  date: string;
  day: number;
  minutes: number;
}

const WEEKDAYS: Record<string, number> = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

/** What the clock says in `timeZone` at `at` (ms since 1970, or an ISO string). */
export function localTime(timeZone: string, at: number | string = Date.now()): LocalTime {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(at));
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return {
    date: `${get("year")}-${get("month")}-${get("day")}`,
    day: WEEKDAYS[get("weekday")] ?? 0,
    minutes: Number(get("hour")) * 60 + Number(get("minute")),
  };
}

function toMinutes(t: string): number {
  const [h = 0, m = 0] = t.split(":").map(Number);
  return h * 60 + m;
}

/** Each night's lock as [start, end) in minutes since Monday 00:00. A lock that runs past Sunday midnight ends after WEEK. */
function lockWindows(entries: ScheduleEntry[]): [number, number][] {
  return entries.map((e) => {
    const lock = toMinutes(e.lock_at);
    const unlock = toMinutes(e.unlock_at);
    const start = e.day_of_week * DAY + lock;
    return [start, unlock > lock ? e.day_of_week * DAY + unlock : (e.day_of_week + 1) * DAY + unlock];
  });
}

/** The times the schedule locks the PC on `day`, as [from, to) minutes since that day's midnight. */
export function lockedPeriods(entries: ScheduleEntry[], day: number): [number, number][] {
  const dayStart = day * DAY;
  const out: [number, number][] = [];
  for (const [start, end] of lockWindows(entries)) {
    // Sunday night's lock carries on into Monday morning, so look at each window a week earlier too.
    for (const shift of [0, -WEEK]) {
      const from = Math.max(start + shift, dayStart);
      const to = Math.min(end + shift, dayStart + DAY);
      if (to > from) out.push([from - dayStart, to - dayStart]);
    }
  }
  return out.sort((a, b) => a[0] - b[0]);
}

/** When the next scheduled lock starts: minutes from `now`, and its local time ("21:00"). Null without a schedule. */
export function nextLock(entries: ScheduleEntry[], now: LocalTime): { inMinutes: number; at: string } | null {
  const nowAbs = now.day * DAY + now.minutes;
  let best: number | null = null;
  for (const [start] of lockWindows(entries)) {
    const wait = (((start - nowAbs) % WEEK) + WEEK) % WEEK;
    if (best === null || wait < best) best = wait;
  }
  if (best === null) return null;
  return { inMinutes: best, at: clock((now.minutes + best) % DAY) };
}

/** Extra time that falls on the local date `today`, as [from, to) minutes since midnight. */
export function extraTimePeriods(overrides: Override[], timeZone: string, today: string): [number, number][] {
  const out: [number, number][] = [];
  for (const o of overrides) {
    const s = localTime(timeZone, o.starts_at);
    const e = localTime(timeZone, o.ends_at);
    if (e.date < today || s.date > today) continue;
    const from = s.date < today ? 0 : s.minutes;
    const to = e.date > today ? DAY : e.minutes;
    if (to > from) out.push([from, to]);
  }
  return out;
}


/** "21:30" from minutes since midnight. */
export function clock(minutes: number): string {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}

/**
 * The big line on a PC's card: how long the child has left, e.g. { value: "1 h 42 min", caption: "until bedtime at 21:00" }.
 * value is null when there's nothing to count down to.
 */
export function bedtimeSummary(device: Device, entries: ScheduleEntry[], now: number = Date.now()): { value: string | null; caption: string } {
  if (!device.enrolled) return { value: null, caption: "Not installed yet" };
  const status = deviceStatus(device, now);
  const s = device.last_status ?? {};
  if (status.online && s.mode === "locked") {
    if (s.reason === "schedule" && s.unlock_at) return { value: "Locked", caption: `until ${formatTime(s.unlock_at, device.timezone)}` };
    return { value: "Locked", caption: s.reason === "immediate" ? "by a parent" : "" };
  }
  if (status.online && s.reason === "override" && s.override_ends_at) {
    const left = Math.max(0, Math.round((Date.parse(s.override_ends_at) - now) / 60000));
    return { value: formatDuration(left * 60), caption: `of extra time left, until ${formatTime(s.override_ends_at, device.timezone)}` };
  }
  const next = nextLock(entries, localTime(device.timezone, now));
  if (!next) return { value: null, caption: "No bedtime schedule yet" };
  if (next.inMinutes > 24 * 60) return { value: null, caption: "No bedtime lock in the next 24 hours" };
  return { value: formatDuration(next.inMinutes * 60), caption: `until bedtime at ${next.at}` };
}
