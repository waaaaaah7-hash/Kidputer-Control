import type { ProcessInfo } from "./api";

/** All running copies of one program (Chrome runs many processes), shown as one row. */
export interface AppGroup {
  key: string;
  path: string | null;
  name: string;
  company: string | null;
  processes: number;
  windows: number;
  memoryMb: number;
  startedAt: string | null;
  titles: string[];
  hasWindow: boolean;
  system: boolean;
}

export function appName(p: Pick<ProcessInfo, "description" | "product" | "name">): string {
  return p.description || p.product || p.name;
}

export function groupProcesses(processes: ProcessInfo[]): AppGroup[] {
  const groups = new Map<string, AppGroup>();
  for (const p of processes) {
    const key = (p.path ?? `name:${p.name}`).toLowerCase();
    let g = groups.get(key);
    if (!g) {
      g = {
        key,
        path: p.path,
        name: appName(p),
        company: p.company,
        processes: 0,
        windows: 0,
        memoryMb: 0,
        startedAt: p.started_at,
        titles: [],
        hasWindow: false,
        system: p.system,
      };
      groups.set(key, g);
    }
    g.processes += 1;
    g.memoryMb += p.memory_mb;
    if (p.has_window) {
      g.windows += 1;
      g.hasWindow = true;
    }
    if (p.title) g.titles.push(p.title);
    if (p.started_at && (!g.startedAt || p.started_at < g.startedAt)) g.startedAt = p.started_at;
    g.system = g.system && p.system;
  }
  return [...groups.values()].sort((a, b) => b.memoryMb - a.memoryMb);
}

export function formatMemory(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}
