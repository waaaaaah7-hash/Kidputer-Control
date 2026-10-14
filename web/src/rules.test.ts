import { describeRule, describeWindows, formatDuration } from "./rules";

const w = (day: number, start: string, end: string) => ({ day, start, end });

describe("describeWindows", () => {
  it("joins consecutive days with the same times", () => {
    const weekdays = [0, 1, 2, 3, 4].map((d) => w(d, "16:00", "19:00"));
    const weekend = [5, 6].map((d) => w(d, "10:00", "20:00"));
    expect(describeWindows([...weekend, ...weekdays])).toBe("Mon–Fri 16:00–19:00, Sat–Sun 10:00–20:00");
  });
  it("keeps gaps and single days apart", () => {
    expect(describeWindows([w(0, "16:00", "17:00"), w(2, "16:00", "17:00"), w(6, "09:00", "10:00")])).toBe(
      "Mon 16:00–17:00, Wed 16:00–17:00, Sun 09:00–10:00",
    );
  });
  it("handles several windows a day and none at all", () => {
    expect(describeWindows([w(5, "18:00", "20:00"), w(5, "09:00", "10:00")])).toBe("Sat 09:00–10:00, 18:00–20:00");
    expect(describeWindows([])).toBe("Never");
  });
});

it("describes rules", () => {
  expect(describeRule({ mode: "allow", windows: [] })).toBe("Allowed any time");
  expect(describeRule({ mode: "block", windows: [] })).toBe("Blocked");
  expect(describeRule({ mode: "schedule", windows: [w(0, "16:00", "19:00")] })).toBe("Only Mon 16:00–19:00");
});

it("formats durations", () => {
  expect(formatDuration(0)).toBe("0 min");
  expect(formatDuration(30)).toBe("under a minute");
  expect(formatDuration(45 * 60)).toBe("45 min");
  expect(formatDuration(3600)).toBe("1 h");
  expect(formatDuration(4800)).toBe("1 h 20 min");
});
