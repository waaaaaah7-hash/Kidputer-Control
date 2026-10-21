import { beforeEach, describe, expect, it } from "vitest";
import { applyTheme, getTheme, setTheme } from "./theme";

describe("theme choice", () => {
  beforeEach(() => {
    window.localStorage.clear();
    applyTheme("system");
  });

  it("follows the device until the parent chooses", () => {
    expect(getTheme()).toBe("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });

  it("remembers Light or Dark, and forgets it again for Match this device", () => {
    setTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(getTheme()).toBe("dark");
    setTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    setTheme("system");
    expect(getTheme()).toBe("system");
    expect(document.documentElement.dataset.theme).toBeUndefined();
  });
});
