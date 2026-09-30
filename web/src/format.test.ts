import type { Device } from "./api";
import { deviceStatus, describeWindow, relativeTime, toEntries, toRows } from "./format";

const NOW = Date.parse("2026-01-12T22:00:00Z");

function device(overrides: Partial<Device> = {}): Device {
  return {
    id: "d1",
    name: "Kid PC",
    timezone: "UTC",
    enrolled: true,
    hostname: "KID-PC",
    agent_version: "1.0.0",
    last_seen_at: "2026-01-12T21:59:40Z",
    last_status: { mode: "none", reason: "none" },
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("deviceStatus", () => {
  it("waits for install before enrollment", () => {
    expect(deviceStatus(device({ enrolled: false }), NOW).label).toBe("Waiting for install");
  });

  it("is offline after missing polls", () => {
    const s = deviceStatus(device({ last_seen_at: "2026-01-12T21:50:00Z" }), NOW);
    expect(s).toMatchObject({ label: "Offline", online: false });
  });

  it("shows scheduled lock with unlock time", () => {
    const s = deviceStatus(device({ last_status: { mode: "locked", reason: "schedule", unlock_at: "2026-01-13T07:00:00Z" } }), NOW);
    expect(s.label).toBe("Locked");
    expect(s.tone).toBe("danger");
    expect(s.detail).toMatch(/Scheduled bedtime until 0?7:00/);
  });

  it("puts tamper alerts first", () => {
    const s = deviceStatus(device({ last_status: { mode: "locked", reason: "schedule", tamper_problems: ["modified: Watchdog.ps1"] } }), NOW);
    expect(s.label).toBe("Tamper alert");
  });

  it("describes overrides and countdowns", () => {
    expect(deviceStatus(device({ last_status: { mode: "none", reason: "override", override_ends_at: "2026-01-12T23:00:00Z" } }), NOW).detail).toMatch(/Override until/);
    expect(deviceStatus(device({ last_status: { mode: "countdown", reason: "schedule" } }), NOW).label).toBe("Locking soon");
  });
});

describe("schedule rows", () => {
  it("round-trips entries through editable rows", () => {
    const entries = [
      { day_of_week: 0, lock_at: "21:30:00", unlock_at: "07:00:00" },
      { day_of_week: 5, lock_at: "23:00:00", unlock_at: "08:00:00" },
    ];
    const rows = toRows(entries);
    expect(rows).toHaveLength(7);
    expect(rows[0]).toEqual({ enabled: true, lock_at: "21:30", unlock_at: "07:00" });
    expect(rows[1]?.enabled).toBe(false);
    expect(toEntries(rows)).toEqual([
      { day_of_week: 0, lock_at: "21:30", unlock_at: "07:00" },
      { day_of_week: 5, lock_at: "23:00", unlock_at: "08:00" },
    ]);
  });

  it("describes overnight and same-day windows", () => {
    expect(describeWindow("21:00", "07:00")).toBe("until 07:00 next day · 10 h");
    expect(describeWindow("13:00", "15:30")).toBe("until 15:30 · 2 h 30 min");
  });
});

describe("relativeTime", () => {
  it("handles recent and missing times", () => {
    expect(relativeTime(null)).toBe("never");
    expect(relativeTime("2026-01-12T21:59:50Z", NOW)).toBe("just now");
    expect(relativeTime("2026-01-12T21:30:00Z", NOW)).toMatch(/30 minutes ago/);
  });
});
