import type { ActivityEvent, Device } from "./api";
import { eventLabel, formatDateTime, relativeTime } from "./format";
import { Badge } from "./ui";

const SOURCE_LABEL: Record<ActivityEvent["source"], string> = { agent: "PC", admin: "Parent", system: "System" };

export default function EventList({ events, devices, showDevice = false }: { events: ActivityEvent[]; devices?: Device[]; showDevice?: boolean }) {
  if (!events.length) return <p className="muted">No activity yet.</p>;
  const names = new Map(devices?.map((d) => [d.id, d.name]));
  return (
    <ul className="events">
      {events.map((e) => (
        <li key={e.id} className={`event sev-${e.severity}`}>
          <div className="event-main">
            <span className="event-title">
              {e.severity !== "info" && <Badge tone={e.severity === "critical" ? "danger" : "warn"}>{e.severity}</Badge>} {eventLabel(e)}
            </span>
            {e.message && eventLabel(e) !== e.message && <span className="muted event-message">{e.message}</span>}
          </div>
          <div className="event-meta muted small">
            {showDevice && e.device_id && <span>{names.get(e.device_id) ?? "Deleted device"} · </span>}
            <span>{SOURCE_LABEL[e.source]} · </span>
            <time dateTime={e.occurred_at} title={formatDateTime(e.occurred_at)}>
              {relativeTime(e.occurred_at)}
            </time>
          </div>
        </li>
      ))}
    </ul>
  );
}
