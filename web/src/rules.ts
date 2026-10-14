/** App rules (roadmap #3): the shapes the API uses, and how to describe them in plain words. */

export type RuleMode = "allow" | "block" | "schedule";

export interface RuleWindow {
  day: number; // 0 = Monday
  start: string; // "HH:MM"
  end: string;
}

export interface AppRuleIn {
  target_kind: "app" | "category";
  target: string;
  mode: RuleMode;
  windows: RuleWindow[];
}

export interface AppRule extends AppRuleIn {
  id: string;
  name: string;
}

const SHORT_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

/** "Mon–Fri 16:00–19:00, Sat–Sun 10:00–20:00": consecutive days with the same times are joined. */
export function describeWindows(windows: RuleWindow[]): string {
  if (!windows.length) return "Never";
  const byDay = new Map<number, string>();
  for (const w of [...windows].sort((a, b) => a.day - b.day || a.start.localeCompare(b.start))) {
    const t = `${w.start}–${w.end}`;
    byDay.set(w.day, byDay.has(w.day) ? `${byDay.get(w.day)}, ${t}` : t);
  }
  const parts: string[] = [];
  let runStart = -1;
  let runTimes = "";
  const flush = (end: number) => {
    if (runStart < 0) return;
    const days = runStart === end ? SHORT_DAYS[runStart] : `${SHORT_DAYS[runStart]}–${SHORT_DAYS[end]}`;
    parts.push(`${days} ${runTimes}`);
  };
  for (let d = 0; d <= 7; d++) {
    const t = d < 7 ? byDay.get(d) : undefined;
    if (t !== undefined && runStart >= 0 && t === runTimes) continue;
    flush(d - 1);
    runStart = t === undefined ? -1 : d;
    runTimes = t ?? "";
  }
  return parts.join(", ");
}

export function describeRule(rule: Pick<AppRuleIn, "mode" | "windows">): string {
  if (rule.mode === "allow") return "Allowed any time";
  if (rule.mode === "block") return "Blocked";
  return `Only ${describeWindows(rule.windows)}`;
}

/** "1 h 20 min", "45 min", "under a minute". */
export function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return seconds > 0 ? "under a minute" : "0 min";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (!h) return `${m} min`;
  return m ? `${h} h ${m} min` : `${h} h`;
}
