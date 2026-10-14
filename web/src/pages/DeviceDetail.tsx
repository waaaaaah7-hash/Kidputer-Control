import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type Device, type DeviceSettings, type LockRequest, type Message, type Override, type ScheduleEntry } from "../api";
import { groupProcesses, type AppGroup } from "../apps";
import { bedtimeSummary, clock, extraTimePeriods, localTime, lockedPeriods } from "../bedtime";
import InstallPanel, { ModeChoice } from "../InstallPanel";
import MessageForm from "../MessageForm";
import AppsTab, { CloseDialog } from "../device/AppsTab";
import RulesTab from "../device/RulesTab";
import UsageTab from "../device/UsageTab";
import FilesTab from "../device/FilesTab";
import SecurityTab from "../device/SecurityTab";
import { UserCancelled } from "../cancel";
import { useReauth } from "../reauthContext";
import EventList from "../EventList";
import { Icon } from "../icons";
import { useLiveDevice } from "../live";
import { formatDuration } from "../rules";
import {
  DAYS,
  describeWindow,
  deviceStatus,
  formatDateTime,
  formatTime,
  relativeTime,
  timeZones,
  toEntries,
  toRows,
  type ScheduleRow,
} from "../format";
import { ActionButton, Badge, Card, ErrorNote, Spinner, errorMessage, useLoader, useToast } from "../ui";

interface Live {
  device: Device;
  lock: LockRequest | null;
  overrides: Override[];
  message: Message | null;
  entries: ScheduleEntry[];
}

const TABS = [
  { id: "overview", label: "Overview" },
  { id: "apps", label: "Apps" },
  { id: "rules", label: "App rules" },
  { id: "usage", label: "Usage" },
  { id: "files", label: "Files" },
  { id: "security", label: "Security" },
  { id: "settings", label: "Schedule and settings" },
] as const;
type TabId = (typeof TABS)[number]["id"];

export default function DeviceDetail() {
  const { id = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const tab: TabId = TABS.some((t) => t.id === params.get("tab")) ? (params.get("tab") as TabId) : "overview";
  const live = useLoader<Live>(
    async () => {
      const [device, lock, overrides, message, schedule] = await Promise.all([
        api.device(id),
        api.lock(id),
        api.overrides(id),
        api.message(id),
        api.schedule(id),
      ]);
      return { device, lock, overrides, message, entries: schedule.entries };
    },
    [id],
    10000,
  );
  const events = useLoader(() => api.events({ device_id: id, limit: 15 }), [id], 15000);
  const refresh = async () => {
    await Promise.all([live.reload(), events.reload()]);
  };

  if (live.loading && !live.data) return <Spinner />;
  if (!live.data) return <ErrorNote error={live.error ?? "Device not found"} />;
  const { device } = live.data;

  return (
    <>
      <p className="crumbs">
        <Link to="/">All PCs</Link> /
      </p>
      <Hero live={live.data} onChange={refresh} onMessage={() => focusMessage(device.id, () => setParams({}, { replace: true }))} />
      <ErrorNote error={live.error} />
      <div className="tabs" role="tablist" aria-label="Device sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setParams(t.id === "overview" ? {} : { tab: t.id }, { replace: true })}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === "apps" && <AppsTab device={device} />}
      {tab === "rules" && <RulesTab device={device} />}
      {tab === "usage" && <UsageTab device={device} />}
      {tab === "files" && <FilesTab device={device} />}
      {tab === "security" && <SecurityTab device={device} />}
      {tab === "overview" && (
        <div className="stack">
          {device.enrolled && <DayTimeline live={live.data} />}
          <div className="columns">
            <div className="stack">
              {device.enrolled && <RunningNow device={device} onAll={() => setParams({ tab: "apps" }, { replace: true })} />}
              <MessagePanel device={device} message={live.data.message} onChange={refresh} />
            </div>
            <div className="stack">
              <Card title="Recent activity" actions={<Link to={`/activity?device=${device.id}`}>All activity</Link>}>
                {events.data ? <EventList events={events.data} /> : <Spinner />}
              </Card>
            </div>
          </div>
        </div>
      )}
      {tab === "settings" && (
        <div className="columns">
          <div className="stack">
            <SchedulePanel device={device} onChange={refresh} />
            <DevicePanel device={device} onChange={refresh} />
          </div>
          <div className="stack">
            <SettingsPanel device={device} onChange={refresh} />
          </div>
        </div>
      )}
    </>
  );
}

/** Jump to the message box on the Overview, switching to it first if another section is open. */
function focusMessage(deviceId: string, showOverview: () => void) {
  showOverview();
  window.setTimeout(() => {
    const box = document.getElementById(`message-${deviceId}`);
    box?.scrollIntoView({ behavior: "smooth", block: "center" });
    box?.focus({ preventScroll: true });
  }, 50);
}

// --- Status, countdown, lock now and extra time --------------------------------------------------------

function Hero({ live, onChange, onMessage }: { live: Live; onChange: () => Promise<void>; onMessage: () => void }) {
  const { device, lock, overrides, entries } = live;
  const status = deviceStatus(device);
  const summary = bedtimeSummary(device, entries);
  const s = device.last_status ?? {};
  const [reason, setReason] = useState("");
  const [until, setUntil] = useState("");
  const toast = useToast();
  const id = device.id;

  const warnings: string[] = [];
  if (status.online && s.console_user_is_child && s.ui_agent_alive === false) warnings.push("The lock screen isn't running on the PC right now.");
  if (s.clock_skew_seconds && Math.abs(s.clock_skew_seconds) > 300) warnings.push(`The PC's clock is off by ${Math.round(s.clock_skew_seconds / 60)} minutes.`);
  if (s.pin_locked_until && new Date(s.pin_locked_until).getTime() > Date.now())
    warnings.push(`PIN entry is locked after wrong attempts until ${formatTime(s.pin_locked_until)}.`);
  if (device.mode === "managed" && s.child_is_admin)
    warnings.push("Your child's Windows account is an administrator, so they could switch Kidputer Control off. Make it a standard account in Windows Settings, or switch this PC to Trusted mode.");

  const grant = async (minutes: number) => {
    await api.createOverride(id, { duration_minutes: minutes, reason: "Extra time" });
    await onChange();
  };

  async function grantUntil(e: FormEvent) {
    e.preventDefault();
    try {
      await api.createOverride(id, { ends_at: new Date(until).toISOString(), reason: "Extra time" });
      toast(`Unlocked until ${formatTime(new Date(until).toISOString())}`);
      setUntil("");
      await onChange();
    } catch (err) {
      if (!(err instanceof UserCancelled)) toast(errorMessage(err), "danger");
    }
  }

  return (
    <section className="card hero device-head" aria-labelledby="device-name">
      <div className="hero-top">
        <div className="hero-main">
          <div className="device-status">
            <Badge tone={status.tone}>{status.label}</Badge>
            <span>{status.detail}</span>
          </div>
          <h1 id="device-name">
            {device.name}{" "}
            {device.mode === "trusted" && (
              <span title="The child's account is an administrator: the schedule applies, but the child could switch it off.">
                <Badge tone="info">Trusted</Badge>
              </span>
            )}
          </h1>
          <p className="muted small">
            {device.enrolled
              ? `${device.hostname ?? "PC"} · agent ${device.agent_version ?? "?"}${device.agent_update_available ? ` (updating to ${device.agent_update_available})` : ""} · seen ${relativeTime(device.last_seen_at)} · ${device.timezone}`
              : "Not installed yet: open Schedule and settings to install it."}
          </p>
        </div>
        {summary.value && (
          <div className="hero-countdown">
            <span className={summary.value === "Locked" ? "countdown-value locked" : "countdown-value"}>{summary.value}</span>
            <span className="muted">{summary.caption}</span>
          </div>
        )}
      </div>
      {warnings.map((w) => (
        <p key={w} className="warning-note">
          {w}
        </p>
      ))}

      {lock && (
        <div className="callout tone-danger">
          <p>
            <strong>Locked by a parent</strong> {relativeTime(lock.created_at)}
            {lock.reason ? ` — "${lock.reason}"` : ""}. {lock.applied_at ? "The PC has locked." : "Waiting for the PC to pick it up."}
          </p>
        </div>
      )}
      <div className="hero-actions">
        {lock ? (
          <ActionButton variant="primary" success="Immediate lock cleared" onClick={async () => { await api.clearLock(id); await onChange(); }}>
            End immediate lock
          </ActionButton>
        ) : (
          <ActionButton variant="primary" success="Lock requested" onClick={async () => { await api.requestLock(id); await onChange(); }}>
            <Icon name="lock" /> Lock now
          </ActionButton>
        )}
        <ActionButton variant="outline" success="Granted 30 minutes" onClick={() => grant(30)}>+30 min</ActionButton>
        <ActionButton variant="outline" success="Granted 1 hour" onClick={() => grant(60)}>+1 hour</ActionButton>
        <ActionButton variant="outline" success="Granted 2 hours" onClick={() => grant(120)}>+2 hours</ActionButton>
        <button type="button" className="btn btn-secondary" onClick={onMessage}>
          <Icon name="message" /> Message
        </button>
      </div>

      {overrides.length > 0 && (
        <ul className="list">
          {overrides.map((o) => (
            <li key={o.id} className="list-row">
              <span>
                Extra time {new Date(o.starts_at).getTime() > Date.now() ? `from ${formatDateTime(o.starts_at)} ` : ""}until <strong>{formatDateTime(o.ends_at)}</strong>
                {o.reason && o.reason !== "Extra time" ? <span className="muted"> · {o.reason}</span> : null}
              </span>
              <ActionButton variant="ghost" success="Override cancelled" onClick={async () => { await api.cancelOverride(id, o.id); await onChange(); }}>
                Cancel
              </ActionButton>
            </li>
          ))}
        </ul>
      )}

      <details className="more-options">
        <summary>More: lock with a reason, or unlock until a set time</summary>
        <p className="muted small">Extra time unlocks the PC even during the schedule. An immediate lock still wins.</p>
        <div className="stack">
          {!lock && (
            <div className="row wrap">
              <input aria-label="Reason (optional)" placeholder="Reason, shown on the lock screen" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
              <ActionButton
                variant="danger"
                disabled={!reason.trim()}
                success="Lock requested"
                onClick={async () => {
                  await api.requestLock(id, reason.trim());
                  setReason("");
                  await onChange();
                }}
              >
                Lock with this reason
              </ActionButton>
            </div>
          )}
          <form className="row wrap" onSubmit={grantUntil}>
            <input className="until" type="datetime-local" aria-label="Unlock until" required value={until} onChange={(e) => setUntil(e.target.value)} />
            <button type="submit" className="btn btn-secondary">
              Unlock until then
            </button>
          </form>
        </div>
      </details>
    </section>
  );
}

// --- Today, hour by hour ------------------------------------------------------------------------------

function DayTimeline({ live }: { live: Live }) {
  const { device, entries, overrides } = live;
  const usage = useLoader(() => api.usage(device.id, 1), [device.id], 60000);
  const now = localTime(device.timezone);
  const locked = lockedPeriods(entries, now.day);
  const extra = extraTimePeriods(overrides, device.timezone, now.date);
  const used = usage.data?.find((u) => u.day === now.date)?.total_seconds;
  const pct = (m: number) => `${(m / (24 * 60)) * 100}%`;
  const ranges = (list: [number, number][]) => list.map(([a, b]) => `${clock(a)} to ${clock(b)}`).join(", ");
  const description =
    (locked.length ? `Locked by the schedule ${ranges(locked)}.` : "No scheduled lock today.") +
    (extra.length ? ` Extra time ${ranges(extra)}.` : "") +
    ` It's ${clock(now.minutes)} on the PC.`;

  return (
    <Card title="Today, hour by hour" actions={used !== undefined ? <span className="small"><strong>{formatDuration(used)}</strong> used</span> : undefined}>
      <div className="timeline" role="img" aria-label={description}>
        {locked.map(([a, b]) => (
          <span key={`l${a}`} className="tl-locked" style={{ left: pct(a), width: pct(b - a) }} />
        ))}
        {extra.map(([a, b]) => (
          <span key={`e${a}`} className="tl-extra" style={{ left: pct(a), width: pct(b - a) }} />
        ))}
        <span className="tl-now" style={{ left: pct(now.minutes) }} />
      </div>
      <div className="timeline-scale" aria-hidden="true">
        <span>00:00</span>
        <span>06:00</span>
        <span>12:00</span>
        <span>18:00</span>
        <span>24:00</span>
      </div>
      <ul className="legend" aria-hidden="true">
        <li><span className="swatch tl-allowed" /> Allowed</li>
        <li><span className="swatch tl-locked" /> Locked by the schedule</li>
        <li><span className="swatch tl-extra" /> Extra time</li>
        <li><span className="swatch-line" /> Now, {clock(now.minutes)}</li>
      </ul>
    </Card>
  );
}

// --- Open right now -----------------------------------------------------------------------------------

function RunningNow({ device, onAll }: { device: Device; onAll: () => void }) {
  useLiveDevice(device.id);
  const report = useLoader(() => api.processes(device.id), [device.id], 5000);
  const [closing, setClosing] = useState<AppGroup | null>(null);
  const apps = groupProcesses(report.data?.processes ?? []).filter((g) => g.hasWindow && !g.system);
  const updated = report.data?.updated_at;
  const fresh = updated ? Date.now() - new Date(updated).getTime() < 60000 : false;

  return (
    <Card title="Open right now" actions={<button type="button" className="link-button" onClick={onAll}>All apps</button>}>
      {report.data === undefined ? (
        <Spinner />
      ) : !updated || !fresh ? (
        <p className="muted">{deviceStatus(device).online ? "Asking the PC…" : "The PC isn't reporting right now."}</p>
      ) : apps.length === 0 ? (
        <p className="muted">No apps open{device.last_status?.console_user_is_child === false ? ": your child isn't signed in." : "."}</p>
      ) : (
        <ul className="list">
          {apps.slice(0, 6).map((g) => (
            <li key={g.key} className="list-row">
              <span className="grow">
                <strong>{g.name}</strong>
                {g.windows > 1 && <span className="muted small"> · {g.windows} windows</span>}
              </span>
              {g.path && (
                <button type="button" className="btn btn-secondary" onClick={() => setClosing(g)}>
                  Close
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <CloseDialog device={device} group={closing} onDone={() => setClosing(null)} />
    </Card>
  );
}

// --- Message ---------------------------------------------------------------------------------------

function MessagePanel({ device, message, onChange }: { device: Device; message: Message | null; onChange: () => Promise<void> }) {
  return (
    <Card title="Message on screen">
      {message && (
        <div className="callout tone-info">
          <p className="message-body">{message.body}</p>
          <p className="muted small">
            {message.mode === "persistent" ? "Stays until you remove it" : "Child can close it"} ·{" "}
            {message.displayed_at ? `shown ${relativeTime(message.displayed_at)}` : "not shown yet"}
          </p>
          <ActionButton success="Message removed" onClick={async () => { await api.clearMessage(device.id); await onChange(); }}>
            Remove message
          </ActionButton>
        </div>
      )}
      <MessageForm deviceId={device.id} replacing={message !== null} onSent={onChange} />
    </Card>
  );
}

// --- Schedule ----------------------------------------------------------------------------------------

function SchedulePanel({ device, onChange }: { device: Device; onChange: () => Promise<void> }) {
  const loaded = useLoader(() => api.schedule(device.id), [device.id]);
  const [rows, setRows] = useState<ScheduleRow[] | null>(null);
  useEffect(() => {
    if (loaded.data) setRows(toRows(loaded.data.entries));
  }, [loaded.data]);

  if (!rows) return <Card title="Bedtime schedule">{loaded.error ? <ErrorNote error={loaded.error} /> : <Spinner />}</Card>;

  const update = (day: number, patch: Partial<ScheduleRow>) => setRows(rows.map((r, i) => (i === day ? { ...r, ...patch } : r)));
  const copyFirstToWeekdays = () => {
    const src = rows[0];
    if (src) setRows(rows.map((r, i) => (i >= 1 && i <= 4 ? { ...src } : r)));
  };
  const dirty = loaded.data ? JSON.stringify(toEntries(rows)) !== JSON.stringify(toEntries(toRows(loaded.data.entries))) : false;

  return (
    <Card title="Bedtime schedule" actions={<span className="muted small">{device.timezone}</span>}>
      <p className="muted small">The PC shows a countdown, then locks from the lock time until the unlock time.</p>
      <table className="schedule">
        <thead>
          <tr>
            <th scope="col">Night</th>
            <th scope="col">Lock at</th>
            <th scope="col">Unlock at</th>
            <th scope="col" className="hide-narrow">
              Window
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, day) => (
            <tr key={DAYS[day]} className={r.enabled ? "" : "off"}>
              <th scope="row">
                <label className="inline">
                  <input type="checkbox" checked={r.enabled} onChange={(e) => update(day, { enabled: e.target.checked })} /> {DAYS[day]}
                </label>
              </th>
              <td>
                <input type="time" aria-label={`${DAYS[day]} lock time`} disabled={!r.enabled} value={r.lock_at} onChange={(e) => update(day, { lock_at: e.target.value })} />
              </td>
              <td>
                <input type="time" aria-label={`${DAYS[day]} unlock time`} disabled={!r.enabled} value={r.unlock_at} onChange={(e) => update(day, { unlock_at: e.target.value })} />
              </td>
              <td className="muted small hide-narrow">{r.enabled ? (r.lock_at === r.unlock_at ? "Times must differ" : describeWindow(r.lock_at, r.unlock_at)) : "No lock"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="form-actions">
        <button type="button" className="btn btn-ghost" onClick={copyFirstToWeekdays}>
          Copy Monday to Tue–Fri
        </button>
        <ActionButton
          variant="primary"
          disabled={!dirty}
          success="Schedule saved"
          onClick={async () => {
            await api.putSchedule(device.id, toEntries(rows));
            await loaded.reload();
            await onChange();
          }}
        >
          Save schedule
        </ActionButton>
      </div>
    </Card>
  );
}

// --- Settings & PIN ----------------------------------------------------------------------------------

function SettingsPanel({ device, onChange }: { device: Device; onChange: () => Promise<void> }) {
  const loaded = useLoader(() => api.settings(device.id), [device.id]);
  const [form, setForm] = useState<DeviceSettings | null>(null);
  const [pin, setPin] = useState("");
  const [pin2, setPin2] = useState("");
  const toast = useToast();
  const reauth = useReauth();
  useEffect(() => {
    if (loaded.data) setForm(loaded.data);
  }, [loaded.data]);

  if (!form) return <Card title="Settings">{loaded.error ? <ErrorNote error={loaded.error} /> : <Spinner />}</Card>;

  const num = (key: keyof DeviceSettings, scale = 1) => ({
    value: String(Math.round((form[key] as number) / scale)),
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: Number(e.target.value) * scale }),
  });

  async function savePin(e: FormEvent) {
    e.preventDefault();
    if (pin !== pin2) {
      toast("The PINs don't match", "danger");
      return;
    }
    try {
      await reauth(() => api.setPin(device.id, pin));
      setPin("");
      setPin2("");
      toast("PIN saved. The PC picks it up on its next sync.");
      await loaded.reload();
      await onChange();
    } catch (err) {
      toast(errorMessage(err), "danger");
    }
  }

  return (
    <Card title="Settings">
      <div className="stack">
        <label className="inline switch">
          <input type="checkbox" checked={form.enforcement_enabled} onChange={(e) => setForm({ ...form, enforcement_enabled: e.target.checked })} />
          Enforce the schedule on this PC
        </label>
        <label className="inline switch">
          <input type="checkbox" checked={form.auto_update} onChange={(e) => setForm({ ...form, auto_update: e.target.checked })} />
          Update Kidputer Control on this PC automatically
        </label>
        <div className="fields">
          <label>
            Countdown before locking (minutes)
            <input type="number" min={0} max={60} {...num("countdown_seconds", 60)} />
          </label>
          <label>
            PIN unlocks for (minutes)
            <input type="number" min={1} max={1440} {...num("pin_override_minutes")} />
          </label>
          <label>
            Wrong PINs before lockout
            <input type="number" min={1} max={20} {...num("pin_max_attempts")} />
          </label>
          <label>
            PIN lockout (minutes)
            <input type="number" min={1} max={1440} {...num("pin_lockout_minutes")} />
          </label>
          <label>
            Alert me if the PC goes silent for (minutes, 0 = never)
            <input type="number" min={0} max={1440} {...num("silent_alert_minutes")} />
          </label>
          <label>
            Scan drives for folder sizes every (days, 0 = only when asked)
            <input type="number" min={0} max={90} {...num("scan_every_days")} />
          </label>
          <label>
            Check for changes every (seconds)
            <input type="number" min={5} max={3600} {...num("poll_interval_seconds")} />
          </label>
        </div>
        <div className="form-actions">
          <ActionButton
            variant="primary"
            success="Settings saved"
            onClick={async () => {
              const { pin_set: _p, updated_at: _u, ...patch } = form;
              await api.patchSettings(device.id, patch);
              await loaded.reload();
              await onChange();
            }}
          >
            Save settings
          </ActionButton>
        </div>
      </div>

      <h3>Lock-screen PIN</h3>
      <p className="muted small">
        {form.pin_set ? "A PIN is set. Entering it at the lock screen unlocks the PC and ends an immediate lock." : "No PIN is set: the PC can only be unlocked from here."}
      </p>
      <form className="row wrap" onSubmit={savePin}>
        <input type="password" inputMode="numeric" autoComplete="new-password" pattern="\d{4,10}" title="4 to 10 digits" required placeholder="New PIN (4–10 digits)" aria-label="New PIN" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))} />
        <input type="password" inputMode="numeric" autoComplete="new-password" pattern="\d{4,10}" required placeholder="Repeat PIN" aria-label="Repeat PIN" value={pin2} onChange={(e) => setPin2(e.target.value.replace(/\D/g, ""))} />
        <button type="submit" className="btn btn-secondary">
          {form.pin_set ? "Change PIN" : "Set PIN"}
        </button>
        {form.pin_set && (
          <ActionButton variant="ghost" confirm="Remove the lock-screen PIN?" success="PIN removed" onClick={async () => { await api.clearPin(device.id); await loaded.reload(); }}>
            Remove PIN
          </ActionButton>
        )}
      </form>
    </Card>
  );
}

// --- Device admin ------------------------------------------------------------------------------------

function DevicePanel({ device, onChange }: { device: Device; onChange: () => Promise<void> }) {
  const [name, setName] = useState(device.name);
  const [tz, setTz] = useState(device.timezone);
  const [mode, setMode] = useState(device.mode);
  const navigate = useNavigate();

  return (
    <Card title="Device">
      <div className="fields">
        <label>
          Name
          <input maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          Time zone
          <select value={tz} onChange={(e) => setTz(e.target.value)}>
            {timeZones().map((z) => (
              <option key={z}>{z}</option>
            ))}
          </select>
        </label>
      </div>
      <ModeChoice mode={mode} onChange={setMode} />
      {mode !== device.mode && (
        <p className="muted small">
          {mode === "trusted"
            ? "Switching needs no reinstall. Make the child's account an administrator in Windows Settings if it isn't already."
            : "Switching needs no reinstall. Make the child's account a standard user in Windows Settings so they can't switch Kidputer Control off."}
        </p>
      )}
      <div className="form-actions">
        <ActionButton
          disabled={!name.trim() || (name === device.name && tz === device.timezone && mode === device.mode)}
          success="Device updated"
          onClick={async () => {
            await api.updateDevice(device.id, { name: name.trim(), timezone: tz, mode });
            await onChange();
          }}
        >
          Save
        </ActionButton>
      </div>

      {device.enrolled ? (
        <details className="reinstall">
          <summary>Reinstall or move to another PC</summary>
          <p className="muted small">Only needed to repair or move the installation: a new installer disconnects the PC that's installed now.</p>
          <InstallPanel deviceId={device.id} enrolled initialMode={device.mode} onChange={onChange} />
        </details>
      ) : (
        <>
          <h3>Install on the PC</h3>
          <InstallPanel deviceId={device.id} enrolled={false} initialMode={device.mode} onChange={onChange} />
        </>
      )}
      <div className="form-actions">
        <ActionButton
          variant="danger"
          confirm={`Delete "${device.name}" and all of its history? The PC stops receiving changes but keeps enforcing its last schedule until you run Uninstall.ps1 on it.`}
          onClick={async () => {
            await api.deleteDevice(device.id);
            navigate("/", { replace: true });
          }}
        >
          Delete device
        </ActionButton>
      </div>
    </Card>
  );
}
