import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, type Device, type EnrollmentCode } from "../api";
import InstallPanel from "../InstallPanel";
import { browserTimeZone, deviceStatus, relativeTime, timeZones } from "../format";
import { Badge, Card, Dialog, ErrorNote, Spinner, errorMessage, useLoader } from "../ui";

export default function Devices() {
  const devices = useLoader(() => api.devices(), [], 15000);
  const [adding, setAdding] = useState(false);

  return (
    <>
      <div className="page-head">
        <h1>Devices</h1>
        <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
          Add a PC
        </button>
      </div>
      <ErrorNote error={devices.error} />
      {devices.loading && !devices.data && <Spinner />}
      {devices.data?.length === 0 && (
        <Card>
          <p>No PCs yet. Add your child's PC to get an enrollment code for the installer.</p>
        </Card>
      )}
      <div className="grid">
        {devices.data?.map((d) => <DeviceCard key={d.id} device={d} />)}
      </div>
      <AddDeviceDialog open={adding} onClose={() => { setAdding(false); void devices.reload(); }} />
    </>
  );
}

function DeviceCard({ device }: { device: Device }) {
  const status = deviceStatus(device);
  return (
    <Link to={`/devices/${device.id}`} className="card device-card">
      <div className="device-card-head">
        <h2>{device.name}</h2>
        <Badge tone={status.tone}>{status.label}</Badge>
      </div>
      <p>{status.detail}</p>
      <p className="muted small">
        {device.enrolled ? `${device.hostname ?? "PC"} · seen ${relativeTime(device.last_seen_at)}` : "Not installed yet"}
      </p>
    </Link>
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
            <Link className="btn btn-secondary" to={`/devices/${created.device_id}`} onClick={close}>
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
