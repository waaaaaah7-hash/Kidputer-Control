import { useState } from "react";
import { api, type AppCatalog, type Device, type UsageDay } from "../api";
import { formatDuration } from "../rules";
import { Card, ErrorNote, Spinner, useLoader } from "../ui";

/** Time per app (roadmap #5): only the app in front counts, and not while the child is idle. */
export default function UsageTab({ device }: { device: Device }) {
  const usage = useLoader(() => api.usage(device.id, 7), [device.id], 60000);
  const catalog = useLoader(() => api.appCatalog(), []);
  const [selected, setSelected] = useState<string | null>(null);

  if (usage.data === undefined) return <Card title="Usage">{usage.error ? <ErrorNote error={usage.error} /> : <Spinner />}</Card>;
  const days = usage.data;
  if (days.length === 0) {
    return (
      <Card title="Usage">
        <p className="muted">No usage yet. It appears here a few minutes after your child uses the PC.</p>
      </Card>
    );
  }
  const day = days.find((d) => d.day === selected) ?? days[0]!;
  const max = Math.max(...days.map((d) => d.total_seconds), 1);

  return (
    <div className="columns">
      <div className="stack">
        <Card title={dayLabel(day.day)} actions={<strong>{formatDuration(day.total_seconds)}</strong>}>
          <p className="muted small">Time with something on screen in front and the child active. Idle time and the lock screen don't count.</p>
          <ByCategory day={day} catalog={catalog.data} />
          <h3>Apps</h3>
          <Bars items={day.apps.slice(0, 15).map((a) => ({ key: a.key, label: a.name, seconds: a.seconds }))} max={day.apps[0]?.seconds ?? 1} testId="usage-apps" />
          {day.apps.length > 15 && <p className="muted small">…and {day.apps.length - 15} more.</p>}
        </Card>
      </div>
      <div className="stack">
        <Card title="Last 7 days">
          <ul className="bars" data-testid="usage-days">
            {days.map((d) => (
              <li key={d.day}>
                <button type="button" className={d.day === day.day ? "bar-row active" : "bar-row"} onClick={() => setSelected(d.day)}>
                  <span className="bar-label">{dayLabel(d.day)}</span>
                  <span className="bar-track">
                    <span className="bar-fill" style={{ width: `${Math.round((d.total_seconds / max) * 100)}%` }} />
                  </span>
                  <span className="bar-value">{formatDuration(d.total_seconds)}</span>
                </button>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

function ByCategory({ day, catalog }: { day: UsageDay; catalog?: AppCatalog }) {
  const totals = new Map<string, number>();
  for (const a of day.apps) {
    const label = a.category ? (catalog?.categories[a.category] ?? a.category) : "Other";
    totals.set(label, (totals.get(label) ?? 0) + a.seconds);
  }
  const items = [...totals.entries()].sort((a, b) => b[1] - a[1]).map(([label, seconds]) => ({ key: label, label, seconds }));
  if (!items.length) return null;
  return (
    <>
      <h3>By category</h3>
      <Bars items={items} max={items[0]!.seconds} testId="usage-categories" />
    </>
  );
}

function Bars({ items, max, testId }: { items: { key: string; label: string; seconds: number }[]; max: number; testId: string }) {
  return (
    <ul className="bars" data-testid={testId}>
      {items.map((i) => (
        <li key={i.key} className="bar-row">
          <span className="bar-label">{i.label}</span>
          <span className="bar-track">
            <span className="bar-fill" style={{ width: `${Math.max(2, Math.round((i.seconds / Math.max(max, 1)) * 100))}%` }} />
          </span>
          <span className="bar-value">{formatDuration(i.seconds)}</span>
        </li>
      ))}
    </ul>
  );
}

function dayLabel(iso: string): string {
  const today = new Date();
  const d = new Date(`${iso}T12:00:00`);
  const diff = Math.round((new Date(today.toDateString()).getTime() - new Date(d.toDateString()).getTime()) / 86400000);
  if (diff === 0) return "Today";
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
}
