import type { ProcessInfo } from "./api";
import { formatMemory, groupProcesses } from "./apps";

function p(over: Partial<ProcessInfo>): ProcessInfo {
  return {
    pid: 1, name: "chrome", path: "C:\\Chrome\\chrome.exe", started_at: "2026-01-12T20:00:00Z", memory_mb: 100,
    has_window: false, title: null, description: "Google Chrome", product: "Google Chrome", company: "Google LLC", system: false, ...over,
  };
}

describe("groupProcesses", () => {
  it("groups copies of a program by path, ignoring case, biggest first", () => {
    const g = groupProcesses([
      p({ pid: 1, has_window: true, title: "YouTube", started_at: "2026-01-12T20:05:00Z" }),
      p({ pid: 2, path: "c:\\chrome\\CHROME.EXE", memory_mb: 50, started_at: "2026-01-12T20:01:00Z" }),
      p({ pid: 3, name: "javaw", path: "C:\\Games\\javaw.exe", description: null, product: "Minecraft", memory_mb: 900, has_window: true }),
    ]);
    expect(g.map((x) => x.name)).toEqual(["Minecraft", "Google Chrome"]);
    const chrome = g[1]!;
    expect([chrome.processes, chrome.windows, chrome.memoryMb]).toEqual([2, 1, 150]);
    expect(chrome.titles).toEqual(["YouTube"]);
    expect(chrome.startedAt).toBe("2026-01-12T20:01:00Z");
  });

  it("falls back to the exe name when there is no path or description", () => {
    const g = groupProcesses([p({ path: null, description: null, product: null, name: "mystery", system: true })]);
    expect(g[0]!.name).toBe("mystery");
    expect(g[0]!.system).toBe(true);
  });
});

it("formats memory", () => {
  expect(formatMemory(512)).toBe("512 MB");
  expect(formatMemory(1536)).toBe("1.5 GB");
});
