import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Device, type EnrollmentCode } from "../api";
import { bedtimeSummary, localTime } from "../bedtime";
import EventList from "../EventList";
import InstallPanel from "../InstallPanel";
import MessageForm from "../MessageForm";
import { browserTimeZone, deviceStatus, timeZones } from "../format";
import { Icon } from "../icons";
import { formatDuration } from "../rules";
import { ActionButton, Badge, Card, Dialog, ErrorNote, Spinner, errorMessage, useLoader } from "../ui";

export default function Devices() {
  const devices = useLoader(() => api.devices(), [], 15000);
  const events = useLoader(() => api.events({ limit: 8 }), [], 30000);
  const [adding, setAdding] = useState(false);
  const reload = async () => {
    await Promise.all([devices.reload(), events.reload()]);
  };

  return (
    <>
      <div className="page-head">
        <div>
          <p className="eyebrow">{new Intl.DateTimeFormat(undefined, { weekday: "long", day: "numeric", month: "long" }).format(new Date())}</p>
          <h1>Your PCs</h1>
        </div>
        <button type="button" className="btn btn-secondary" onClick={() => setAdding(true)}>
          Add a PC
        </button>
      </div>
      <ErrorNote error={devices.error} />
      {devices.loading && !devices.data && <Spinner />}
      <div className="home">
        <div className="home-pcs">
          {devices.data?.length === 0 && (
            <Card>
              <p>No PCs yet. Add your child's PC to get an enrollment code for the installer.</p>
              <p className="small">
                New to Kidputer Control? The <Link to="/help">video guides</Link> show the setup step by step.
              </p>
            </Card>
          )}
          {devices.data?.map((d) => <PcCard key={d.id} device={d} onChange={reload} />)}
        </div>
        {(devices.data?.length ?? 0) > 0 && (
          <Card title="Today" className="home-feed" actions={<Link to="/activity">All activity</Link>}>
            {events.data ? <EventList events={events.data} devices={devices.data} showDevice /> : <Spinner />}
          </Card>
        )}
      </div>
      <AddDeviceDialog open={adding} onClose={() => { setAdding(false); void devices.reload(); }} />
    </>
  );
}

const SECURITY_TONE = { ok: "ok", attention: "warn", unknown: "muted" } as const;

/** One PC: how long until bedtime, and the three things parents do most, as big buttons. */
function PcCard({ device, onChange }: { device: Device; onChange: () => Promise<void> }) {
  const extra = useLoader(
    async () => {
      if (!device.enrolled) return null;
      const [schedule, lock, usage] = await Promise.all([api.schedule(device.id), api.lock(device.id), api.usage(device.id, 1)]);
      return { entries: schedule.entries, lock, usage };
    },
    [device.id],
    30000,
  );
  const [messaging, setMessaging] = useState(false);
  const status = deviceStatus(device);
  const summary = bedtimeSummary(device, extra.data?.entries ?? []);
  const today = localTime(device.timezone).date;
  const usedToday = extra.data?.usage.find((u) => u.day === today)?.total_seconds ?? 0;
  const href = `/devices/${device.id}`;
  const refresh = async () => {
    await Promise.all([extra.reload(), onChange()]);
  };

  return (
    <section className="card pc-card device-card" aria-labelledby={`pc-${device.id}`}>
      <div className="pc-card-head">
        <h2 id={`pc-${device.id}`}>
          <Link to={href}>{device.name}</Link>
        </h2>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      {device.enrolled && extra.data !== undefined && (
        <p className="countdown">
          {summary.value && <span className={summary.value === "Locked" ? "countdown-value locked" : "countdown-value"}>{summary.value}</span>} <span className="muted">{summary.caption}</span>
        </p>
      )}
      {!device.enrolled ? (
        <>
          <p className="muted">Not installed yet.</p>
          <Link className="btn btn-primary align-start" to={`${href}?tab=settings`}>
            Finish setting up
          </Link>
        </>
      ) : (
        <>
          {!status.online && <p className="muted small">{status.detail}</p>}
          <div className="big-actions">
            {extra.data?.lock ? (
              <ActionButton variant="primary" success="Immediate lock cleared" onClick={async () => { await api.clearLock(device.id); await refresh(); }}>
                <Icon name="lock" /> End lock
              </ActionButton>
            ) : (
              <ActionButton variant="primary" success={`Locking ${device.name}`} onClick={async () => { await api.requestLock(device.id); await refresh(); }}>
                <Icon name="lock" /> Lock now
              </ActionButton>
            )}
            <ActionButton success="Granted 30 minutes" onClick={async () => { await api.createOverride(device.id, { duration_minutes: 30, reason: "Extra time" }); await refresh(); }}>
              <Icon name="clock" /> +30 min
            </ActionButton>
            <button type="button" className="btn btn-secondary" onClick={() => setMessaging(true)}>
              <Icon name="message" /> Message
            </button>
          </div>
          {device.security && (
            <Link className={`pc-card-security tone-${SECURITY_TONE[device.security.level]}`} to={`${href}?tab=security`}>
              <Icon name="shield" size={18} />
              <span className="grow">{device.security.text}</span>
            </Link>
          )}
          <ErrorNote error={extra.error} />
          <Link className="pc-card-foot" to={href}>
            <span>
              {extra.data ? (
                <>
                  <strong>{formatDuration(usedToday)}</strong> <span className="muted">on the PC today</span>
                </>
              ) : (
                <span className="muted">Open {device.name}</span>
              )}
            </span>
            <Icon name="chevron" size={18} />
          </Link>
          {device.mode === "trusted" && <p className="muted small">Trusted: the child could switch it off.</p>}
        </>
      )}
      <Dialog open={messaging} onClose={() => setMessaging(false)} title={`Message on ${device.name}`}>
        <MessageForm deviceId={device.id} autoFocus onSent={() => setMessaging(false)} />
      </Dialog>
    </section>
  );
}

function AddDeviceDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [name, setName] = useState("");
  const [tz, setTz] = useState(browserTimeZone());
  const [created, setCreated] = useState<EnrollmentCode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function close() {
    setName("");
    setCreated(null);
    setError(null);
    onClose();
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setCreated(await api.createDevice(name.trim(), tz));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={close} title={created ? "Install on the PC" : "Add a PC"}>
      {created ? (
        <>
          <InstallPanel deviceId={created.device_id} enrolled={false} initialCode={created} />
          <div className="form-actions">
            <Link className="btn btn-secondary" to={`/devices/${created.device_id}?tab=settings`} onClick={close}>
              Set the schedule
            </Link>
          </div>
        </>
      ) : (
        <form onSubmit={submit} className="stack">
          <label>
            Name
            <input required maxLength={100} placeholder="e.g. Sam's laptop" value={name} onChange={(e) => setName(e.target.value)} autoFocus />
          </label>
          <label>
            Time zone (the schedule uses this)
            <select value={tz} onChange={(e) => setTz(e.target.value)}>
              {timeZones().map((z) => (
                <option key={z}>{z}</option>
              ))}
            </select>
          </label>
          <ErrorNote error={error} />
          <div className="form-actions">
            <button type="button" className="btn btn-ghost" onClick={close}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              Create
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
