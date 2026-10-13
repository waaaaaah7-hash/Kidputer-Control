import type { Device } from "./api";
import { authMethodLabel, describeUserAgent, deviceStatus, describeWindow, formatBytes, relativeTime, toEntries, toRows } from "./format";

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
    mode: "managed",
    agent_update_available: null,
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

describe("describeUserAgent", () => {
  it("names common browsers and systems", () => {
    expect(describeUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36")).toBe("Chrome on Windows");
    expect(describeUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36 Edg/141.0")).toBe("Edge on Windows");
    expect(describeUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")).toBe("Safari on iPhone");
    expect(describeUserAgent("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/141.0 Mobile/15E148 Safari/604.1")).toBe("Chrome on iPhone");
    expect(describeUserAgent("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:131.0) Gecko/20100101 Firefox/131.0")).toBe("Firefox on Mac");
    expect(describeUserAgent("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36")).toBe("Chrome on Android");
    expect(describeUserAgent(null)).toBe("Unknown device");
  });

  it("labels sign-in methods", () => {
    expect(authMethodLabel("passkey")).toBe("Passkey");
    expect(authMethodLabel("password_remembered")).toBe("Password, remembered device");
    expect(authMethodLabel(null)).toBe("Signed in");
  });
});

describe("formatBytes", () => {
  it("uses the biggest sensible unit", () => {
    expect(formatBytes(0)).toBe("0 bytes");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(340 * 1024 * 1024)).toBe("340 MB");
    expect(formatBytes(1.25 * 1024 ** 3)).toBe("1.3 GB");
    expect(formatBytes(2 * 1024 ** 4)).toBe("2 TB");
  });
});
