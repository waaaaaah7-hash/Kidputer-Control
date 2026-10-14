import { describe, expect, it } from "vitest";
import type { Device, Override, ScheduleEntry } from "./api";
import { bedtimeSummary, extraTimePeriods, localTime, lockedPeriods, nextLock } from "./bedtime";

// Monday to Friday: lock 21:00, unlock 07:00 the next morning. Saturday: 22:30 to 08:00. Sunday: none.
const SCHOOL: ScheduleEntry[] = [
  ...[0, 1, 2, 3, 4].map((d) => ({ day_of_week: d, lock_at: "21:00", unlock_at: "07:00" })),
  { day_of_week: 5, lock_at: "22:30:00", unlock_at: "08:00:00" },
];

describe("localTime", () => {
  it("reads the clock in the PC's time zone", () => {
    // 2026-01-12 is a Monday. 22:30 UTC is already Tuesday morning in Auckland (UTC+13 in January).
    expect(localTime("UTC", "2026-01-12T22:30:00Z")).toEqual({ date: "2026-01-12", day: 0, minutes: 22 * 60 + 30 });
    expect(localTime("Pacific/Auckland", "2026-01-12T22:30:00Z")).toEqual({ date: "2026-01-13", day: 1, minutes: 11 * 60 + 30 });
  });
});

describe("lockedPeriods", () => {
  it("covers the morning from last night and the evening from tonight", () => {
    expect(lockedPeriods(SCHOOL, 1)).toEqual([
      [0, 7 * 60],
      [21 * 60, 24 * 60],
    ]);
  });

  it("handles a day without its own lock, and Sunday night running into Monday", () => {
    // Sunday: only Saturday night's lock until 08:00.
    expect(lockedPeriods(SCHOOL, 6)).toEqual([[0, 8 * 60]]);
    // Monday morning: nothing from Sunday night, so only Monday evening.
    expect(lockedPeriods(SCHOOL, 0)).toEqual([[21 * 60, 24 * 60]]);
    const sundayNight: ScheduleEntry[] = [{ day_of_week: 6, lock_at: "20:00", unlock_at: "06:30" }];
    expect(lockedPeriods(sundayNight, 0)).toEqual([[0, 6 * 60 + 30]]);
  });

  it("handles a lock that starts and ends on the same day", () => {
    expect(lockedPeriods([{ day_of_week: 2, lock_at: "13:00", unlock_at: "15:00" }], 2)).toEqual([[13 * 60, 15 * 60]]);
  });
});

describe("nextLock", () => {
  it("counts down to tonight's lock", () => {
    expect(nextLock(SCHOOL, { date: "", day: 1, minutes: 19 * 60 + 18 })).toEqual({ inMinutes: 102, at: "21:00" });
  });

  it("skips to the next night with a lock, wrapping round the week", () => {
    // Sunday 10:00: Monday 21:00 is 35 hours away.
    expect(nextLock(SCHOOL, { date: "", day: 6, minutes: 10 * 60 })).toEqual({ inMinutes: 35 * 60, at: "21:00" });
  });

  it("is null without a schedule", () => {
    expect(nextLock([], { date: "", day: 0, minutes: 0 })).toBeNull();
  });
});

describe("extraTimePeriods", () => {
  const o = (starts_at: string, ends_at: string): Override => ({ id: "o", starts_at, ends_at, reason: null, created_at: starts_at, cancelled_at: null });

  it("keeps the part that falls today", () => {
    expect(extraTimePeriods([o("2026-01-12T21:00:00Z", "2026-01-12T21:30:00Z")], "UTC", "2026-01-12")).toEqual([[21 * 60, 21 * 60 + 30]]);
    expect(extraTimePeriods([o("2026-01-11T23:00:00Z", "2026-01-12T01:00:00Z")], "UTC", "2026-01-12")).toEqual([[0, 60]]);
    expect(extraTimePeriods([o("2026-01-12T23:00:00Z", "2026-01-13T01:00:00Z")], "UTC", "2026-01-12")).toEqual([[23 * 60, 24 * 60]]);
    expect(extraTimePeriods([o("2026-01-13T09:00:00Z", "2026-01-13T10:00:00Z")], "UTC", "2026-01-12")).toEqual([]);
  });
});

describe("bedtimeSummary", () => {
  // Tuesday 2026-01-13, 19:18 UTC.
  const NOW = Date.parse("2026-01-13T19:18:00Z");
  const pc = (last_status: Device["last_status"], enrolled = true): Device => ({
    id: "d1",
    name: "Sam's PC",
    timezone: "UTC",
    enrolled,
    hostname: "SAM-PC",
    agent_version: "1.2.0",
    last_seen_at: "2026-01-13T19:17:50Z",
    last_status,
    created_at: "2026-01-01T00:00:00Z",
    mode: "managed",
    agent_update_available: null,
    security: null,
  });

  it("counts down to bedtime", () => {
    expect(bedtimeSummary(pc({ mode: "none", reason: "none" }), SCHOOL, NOW)).toEqual({ value: "1 h 42 min", caption: "until bedtime at 21:00" });
  });

  it("shows extra time instead while it runs", () => {
    const s = bedtimeSummary(pc({ mode: "none", reason: "override", override_ends_at: "2026-01-13T19:48:00Z" }), SCHOOL, NOW);
    expect(s.value).toBe("30 min");
    expect(s.caption).toMatch(/^of extra time left, until /);
  });

  it("says when the PC is locked, has no schedule or isn't installed", () => {
    expect(bedtimeSummary(pc({ mode: "locked", reason: "immediate" }), SCHOOL, NOW)).toEqual({ value: "Locked", caption: "by a parent" });
    expect(bedtimeSummary(pc({ mode: "locked", reason: "schedule", unlock_at: "2026-01-14T07:00:00Z" }), SCHOOL, NOW).caption).toMatch(/^until /);
    expect(bedtimeSummary(pc({ mode: "none", reason: "none" }), [], NOW)).toEqual({ value: null, caption: "No bedtime schedule yet" });
    expect(bedtimeSummary(pc(null, false), SCHOOL, NOW).value).toBeNull();
  });
});
