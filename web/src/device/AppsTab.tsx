import { useState } from "react";
import { api, type Device, type DeviceCommand } from "../api";
import { formatMemory, groupProcesses, type AppGroup } from "../apps";
import { relativeTime } from "../format";
import { useLiveDevice } from "../live";
import { ActionButton, Badge, Card, Dialog, ErrorNote, Spinner, useLoader, useToast } from "../ui";

/** What's running on the child's PC right now (roadmap #1), with a Close button for each app (#2). */
export default function AppsTab({ device }: { device: Device }) {
  useLiveDevice(device.id);
  const report = useLoader(() => api.processes(device.id), [device.id], 5000);
  const stops = useLoader(() => api.commands(device.id, "stop_app"), [device.id], 3000);
  const [showSystem, setShowSystem] = useState(false);
  const [closing, setClosing] = useState<AppGroup | null>(null);

  const groups = groupProcesses(report.data?.processes ?? []);
  const apps = groups.filter((g) => g.hasWindow && !g.system);
  const background = groups.filter((g) => !g.hasWindow && !g.system);
  const system = groups.filter((g) => g.system);
  const updated = report.data?.updated_at;
  const fresh = updated ? Date.now() - new Date(updated).getTime() < 60000 : false;

  return (
    <div className="columns">
      <div className="stack">
        <Card title="Running apps" actions={updated ? <span className="muted small">updated {relativeTime(updated)}</span> : undefined}>
          <ErrorNote error={report.error} />
          {report.data === undefined ? (
            <Spinner />
          ) : !updated ? (
            <p className="muted">Waiting for the PC. While this page is open it reports every few seconds.</p>
          ) : (
            <>
              {!fresh && <p className="warning-note">This list is from {relativeTime(updated)}: the PC isn't reporting right now.</p>}
              {apps.length === 0 ? (
                <p className="muted">No apps open{device.last_status?.console_user_is_child === false ? ": your child isn't signed in." : "."}</p>
              ) : (
                <ul className="list" data-testid="running-apps">
                  {apps.map((g) => (
                    <AppRow key={g.key} group={g} onClose={() => setClosing(g)} />
                  ))}
                </ul>
              )}
            </>
          )}
        </Card>
        <PendingStops commands={stops.data ?? []} />
      </div>
      <div className="stack">
        <Card title="Background">
          <p className="muted small">Programs without a window, such as updaters and launchers.</p>
          {background.length === 0 ? (
            <p className="muted">None.</p>
          ) : (
            <ul className="list" data-testid="background-apps">
              {background.map((g) => (
                <AppRow key={g.key} group={g} onClose={() => setClosing(g)} />
              ))}
            </ul>
          )}
          <label className="inline">
            <input type="checkbox" checked={showSystem} onChange={(e) => setShowSystem(e.target.checked)} /> Show Windows processes ({system.length})
          </label>
          {showSystem && (
            <ul className="list">
              {system.map((g) => (
                <AppRow key={g.key} group={g} />
              ))}
            </ul>
          )}
        </Card>
        <WindowTitlesCard device={device} enabled={report.data?.window_titles ?? false} onChange={report.reload} />
      </div>
      <CloseDialog device={device} group={closing} onDone={() => { setClosing(null); void stops.reload(); }} />
    </div>
  );
}

function AppRow({ group: g, onClose }: { group: AppGroup; onClose?: () => void }) {
  const parts = [formatMemory(g.memoryMb)];
  if (g.windows > 1) parts.unshift(`${g.windows} windows`);
  if (g.startedAt) parts.push(`started ${relativeTime(g.startedAt)}`);
  return (
    <li className="list-row">
      <div className="grow">
        <strong>{g.name}</strong> {g.company && <span className="muted small">{g.company}</span>}
        <div className="muted small">{parts.join(" · ")}</div>
        {g.titles.length > 0 && <div className="muted small wrap-anywhere">{g.titles.slice(0, 3).join(" · ")}</div>}
      </div>
      {onClose && g.path && (
        <button type="button" className="btn btn-ghost" onClick={onClose}>
          Close
        </button>
      )}
    </li>
  );
}

export function CloseDialog({ device, group, onDone }: { device: Device; group: AppGroup | null; onDone: () => void }) {
  const toast = useToast();
  const close = async (grace: number) => {
    if (!group?.path) return;
    await api.stopApp(device.id, { path: group.path, name: group.name, grace_seconds: grace });
    toast(grace ? `${group.name} will close after a 1-minute warning` : `Closing ${group.name}`);
    onDone();
  };
  return (
    <Dialog open={group !== null} onClose={onDone} title={`Close ${group?.name ?? "app"}?`}>
      <div className="stack">
        <p>
          Your child sees a message first, so they can save. If the app is still open when the minute is up, it's closed. Closing right away
          can lose unsaved work.
        </p>
        <div className="row wrap">
          <ActionButton variant="primary" onClick={() => close(60)}>
            Warn, then close in 1 minute
          </ActionButton>
          <ActionButton variant="danger" onClick={() => close(0)}>
            Close right away
          </ActionButton>
        </div>
      </div>
    </Dialog>
  );
}

const STOP_STATUS: Record<DeviceCommand["status"], string> = {
  pending: "Closing…",
  done: "Closed",
  failed: "Not closed",
  expired: "The PC didn't respond",
};

function PendingStops({ commands }: { commands: DeviceCommand[] }) {
  // Recent ones only: the last 10 minutes.
  const recent = commands.filter((c) => Date.now() - new Date(c.created_at).getTime() < 10 * 60000);
  if (!recent.length) return null;
  return (
    <Card title="Recently closed">
      <ul className="list" data-testid="app-stops">
        {recent.map((c) => {
          const msg = typeof c.result?.message === "string" ? c.result.message : null;
          return (
            <li key={c.id} className="list-row">
              <div className="grow">
                <strong>{String(c.payload.name ?? "App")}</strong>
                <div className="muted small">
                  asked {relativeTime(c.created_at)}
                  {msg ? ` · ${msg}` : ""}
                </div>
              </div>
              <Badge tone={c.status === "done" ? "ok" : c.status === "pending" ? "info" : "warn"}>{STOP_STATUS[c.status]}</Badge>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function WindowTitlesCard({ device, enabled, onChange }: { device: Device; enabled: boolean; onChange: () => Promise<void> }) {
  return (
    <Card title="Window titles">
      <p className="muted small">
        Titles show what's open inside an app, such as browser tabs or document names. They're off by default for your child's privacy.
      </p>
      <ActionButton
        success={enabled ? "Window titles hidden" : "Window titles shown"}
        onClick={async () => {
          await api.patchSettings(device.id, { show_window_titles: !enabled });
          await onChange();
        }}
      >
        {enabled ? "Hide window titles" : "Show window titles"}
      </ActionButton>
    </Card>
  );
}
