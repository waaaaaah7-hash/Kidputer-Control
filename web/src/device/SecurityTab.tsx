import { useEffect, useState } from "react";
import { api, type Device, type DeviceSettings, type SecurityAction, type SecurityState, type Threat } from "../api";
import { formatBytes, formatDateTime, relativeTime, type Tone } from "../format";
import { useLiveDevice } from "../live";
import { ActionButton, Badge, Card, ErrorNote, Spinner, useLoader } from "../ui";

const ACTION_LABELS: Record<SecurityAction, string> = {
  quick_scan: "Quick virus scan",
  full_scan: "Full virus scan",
  update_signatures: "Updating virus definitions",
  remove_threats: "Removing threats",
  check_updates: "Checking for Windows updates",
  install_updates: "Installing Windows updates",
};

const THREAT_STATUS: Record<string, [string, Tone]> = {
  quarantined: ["Quarantined", "ok"],
  removed: ["Removed", "ok"],
  cleaned: ["Cleaned", "ok"],
  blocked: ["Blocked", "ok"],
  allowed: ["Allowed", "warn"],
  detected: ["Needs action", "danger"],
  quarantine_failed: ["Couldn't quarantine", "danger"],
  remove_failed: ["Couldn't remove", "danger"],
  block_failed: ["Couldn't block", "danger"],
  allow_failed: ["Couldn't allow", "warn"],
  abandoned: ["Needs action", "danger"],
  unknown: ["Unknown", "warn"],
};

const SEVERITY_TONE: Record<Threat["severity"], Tone> = { severe: "danger", high: "danger", moderate: "warn", low: "info", unknown: "muted" };

const UPDATE_KIND: Record<string, string> = {
  security: "Security",
  quality: "Monthly",
  definition: "Virus definitions",
  feature: "New Windows version",
  driver: "Driver",
};

const DAY = 24 * 3600 * 1000;

/** Virus protection and Windows updates on the PC (roadmap #23). */
export default function SecurityTab({ device }: { device: Device }) {
  useLiveDevice(device.id);
  const sec = useLoader(() => api.security(device.id), [device.id], 5000);

  if (sec.data === undefined) return <Card title="Security">{sec.error ? <ErrorNote error={sec.error} /> : <Spinner />}</Card>;
  const report = sec.data.report;
  const act = async (action: SecurityAction) => {
    await api.securityAction(device.id, action);
    await sec.reload();
  };

  return (
    <div className="stack">
      <ErrorNote error={sec.error} />
      {!report ? (
        <Card title="Security">
          <p className="muted">
            {device.enrolled
              ? "Waiting for the PC's first security report. It arrives a minute or so after the PC starts, with Kidputer Control 1.3 or later."
              : "Shown once Kidputer Control is installed on the PC."}
          </p>
        </Card>
      ) : (
        <>
          <Activity state={sec.data} />
          <div className="columns">
            <div className="stack">
              <VirusCard state={sec.data} act={act} />
              <ThreatsCard state={sec.data} act={act} />
            </div>
            <div className="stack">
              <UpdatesCard state={sec.data} act={act} />
              <SecuritySettings device={device} />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/** What the PC is doing now, and requests it hasn't picked up yet. */
function Activity({ state }: { state: SecurityState }) {
  const r = state.report!;
  const waiting = state.commands.filter((c) => c.status === "pending");
  const recent = state.commands.filter((c) => c.status === "failed" && c.completed_at && Date.now() - new Date(c.completed_at).getTime() < DAY);
  if (!r.running && !r.queued.length && !waiting.length && !recent.length) return null;
  return (
    <div className="callout tone-info" role="status" data-testid="security-activity">
      {r.running && (
        <p>
          <strong>{ACTION_LABELS[r.running.action]}</strong> on the PC, started {relativeTime(r.running.started_at)}.
          {r.running.action === "full_scan" ? " A full scan can take an hour or more." : ""}
        </p>
      )}
      {r.queued.length > 0 && <p className="small">Next: {r.queued.map((a) => ACTION_LABELS[a]).join(", ")}.</p>}
      {waiting.length > 0 && !r.running && (
        <p className="small">Waiting for the PC to pick up: {waiting.map((c) => ACTION_LABELS[c.payload.action as SecurityAction] ?? "a request").join(", ")}.</p>
      )}
      {recent.map((c) => (
        <p key={c.id} className="small">
          {ACTION_LABELS[c.payload.action as SecurityAction] ?? "A request"} didn't work: {String(c.result?.message || "the PC reported a problem")}.
        </p>
      ))}
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <li className="list-row">
      <span className="grow">{label}</span>
      <span className="nowrap">{children}</span>
    </li>
  );
}

function when(iso: string | null | undefined, never = "Never"): string {
  return iso ? relativeTime(iso) : never;
}

function VirusCard({ state, act }: { state: SecurityState; act: (a: SecurityAction) => Promise<void> }) {
  const d = state.report!.defender;
  const busy = (a: SecurityAction) => state.report!.running?.action === a || state.report!.queued.includes(a);
  const defenderOn = !!d && d.available && d.antivirus_enabled;
  const sigsOld = !!d?.signatures_updated && Date.now() - new Date(d.signatures_updated).getTime() > 3 * DAY;
  const others = d?.other_antivirus ?? [];
  return (
    <Card title="Virus protection" actions={state.updated_at ? <span className="muted small">updated {relativeTime(state.updated_at)}</span> : undefined}>
      {defenderOn ? (
        <ul className="list" data-testid="virus-status">
          <Row label="Real-time protection">{d.realtime ? <Badge tone="ok">On</Badge> : <Badge tone="danger">Off</Badge>}</Row>
          <Row label="Tamper protection">
            {d.tamper_protected === null ? <span className="muted">Unknown</span> : d.tamper_protected ? <Badge tone="ok">On</Badge> : <Badge tone="warn">Off</Badge>}
          </Row>
          <Row label="Virus definitions">{sigsOld ? <Badge tone="warn">{when(d.signatures_updated)}</Badge> : <span>updated {when(d.signatures_updated, "never")}</span>}</Row>
          <Row label="Last quick scan">{when(d.quick_scan_at)}</Row>
          <Row label="Last full scan">{when(d.full_scan_at)}</Row>
        </ul>
      ) : others.length ? (
        <>
          <p>Microsoft Defender has handed over to another antivirus program. Kidputer Control can show whether it's on, but not its scans.</p>
          <ul className="list">
            {others.map((a) => (
              <Row key={a.name} label={a.name}>
                {a.enabled ? <Badge tone="ok">On</Badge> : <Badge tone="danger">Off</Badge>}{" "}
                {a.up_to_date ? <Badge tone="ok">Up to date</Badge> : <Badge tone="warn">Out of date</Badge>}
              </Row>
            ))}
          </ul>
        </>
      ) : (
        <p className="warning-note">No virus protection was found on the PC.</p>
      )}
      {defenderOn && (
        <>
          <div className="row wrap security-buttons">
            <ActionButton variant="primary" disabled={busy("quick_scan")} success="Quick scan requested. It takes about 5 to 15 minutes." onClick={() => act("quick_scan")}>
              Quick scan
            </ActionButton>
            <ActionButton disabled={busy("full_scan")} success="Full scan requested. It runs in the background and can take an hour or more." onClick={() => act("full_scan")}>
              Full scan
            </ActionButton>
            <ActionButton disabled={busy("update_signatures")} success="Virus definitions update requested" onClick={() => act("update_signatures")}>
              Update definitions
            </ActionButton>
          </div>
          <p className="muted small">A quick scan checks where viruses usually hide, in 5 to 15 minutes. A full scan checks everything; it slows the PC a little.</p>
        </>
      )}
    </Card>
  );
}

function ThreatsCard({ state, act }: { state: SecurityState; act: (a: SecurityAction) => Promise<void> }) {
  const d = state.report!.defender;
  if (!d || !d.available) return null;
  const threats = d.threats ?? [];
  const active = threats.filter((t) => t.needs_action);
  return (
    <Card title="Threats found">
      {threats.length === 0 ? (
        <p className="muted">None in Microsoft Defender's history.</p>
      ) : (
        <ul className="list" data-testid="threats">
          {threats.map((t) => {
            const [label, tone] = THREAT_STATUS[t.status] ?? THREAT_STATUS.unknown!;
            return (
              <li key={t.id} className="list-row align-top">
                <div className="grow">
                  <strong className="wrap-anywhere">{t.name}</strong>{" "}
                  {t.severity !== "unknown" && <Badge tone={SEVERITY_TONE[t.severity]}>{t.severity}</Badge>}
                  <div className="muted small">{t.detected_at ? formatDateTime(t.detected_at) : "Date unknown"}</div>
                  {t.files.map((f) => (
                    <div key={f} className="muted small wrap-anywhere">
                      {f}
                    </div>
                  ))}
                </div>
                <Badge tone={tone}>{label}</Badge>
              </li>
            );
          })}
        </ul>
      )}
      {active.length > 0 && (
        <div className="form-actions">
          <ActionButton variant="primary" success="Asked the PC to remove the threats" onClick={() => act("remove_threats")}>
            Remove threats
          </ActionButton>
        </div>
      )}
    </Card>
  );
}

function UpdatesCard({ state, act }: { state: SecurityState; act: (a: SecurityAction) => Promise<void> }) {
  const r = state.report!;
  const u = r.updates;
  const waiting = u.pending.filter((p) => p.kind !== "driver");
  const busy = (a: SecurityAction) => r.running?.action === a || r.queued.includes(a);
  const failed = u.last_install && u.last_install.failed.length > 0 && Date.now() - new Date(u.last_install.at).getTime() < 3 * DAY;
  const statuses: [string, Tone][] = [];
  if (u.service_disabled) statuses.push(["Windows Update is switched off", "danger"]);
  if (u.paused_until) statuses.push([`Paused until ${formatDateTime(u.paused_until)}`, "warn"]);
  if (u.reboot_required) statuses.push(["Restart needed to finish installing", "warn"]);
  if (failed) statuses.push(["The last install failed", "warn"]);
  if (waiting.length) statuses.push([`${waiting.length} waiting`, "info"]);
  if (!statuses.length) statuses.push(["Up to date", "ok"]);

  return (
    <Card title="Windows updates">
      {u.windows && (
        <p className="small">
          <strong>{u.windows.name}</strong>
          {u.windows.version ? ` · ${u.windows.version}` : ""}
          {u.windows.build ? <span className="muted"> · build {u.windows.build}</span> : null}
        </p>
      )}
      <div className="row wrap" data-testid="update-status">
        {statuses.map(([text, tone]) => (
          <Badge key={text} tone={tone}>
            {text}
          </Badge>
        ))}
      </div>
      <ul className="list">
        <Row label="Last checked">{when(u.last_check_at ?? u.last_search_at)}</Row>
        <Row label="Last installed">{when(u.last_installed_at)}</Row>
        {u.pause_blocking && (
          <Row label="Pausing updates">
            {u.pause_blocking === "on" ? (
              <Badge tone="ok">Blocked</Badge>
            ) : u.pause_blocking === "off" ? (
              <span>Allowed</span>
            ) : (
              <span className="muted">Can't be blocked on Windows Home</span>
            )}
          </Row>
        )}
      </ul>
      {u.install_waiting && waiting.length > 0 && (
        <p className="muted small" data-testid="install-waiting">
          {u.install_waiting === "power"
            ? "Waiting to install until the PC is plugged in (the battery is below half)."
            : "Waiting to install until the PC is off a metered connection, such as mobile data."}
        </p>
      )}
      {failed && <p className="warning-note small">Didn't install: {u.last_install!.failed.slice(0, 3).join("; ")}. The PC tries again later.</p>}
      {waiting.length > 0 && (
        <>
          <h3>Waiting to install</h3>
          <ul className="list" data-testid="pending-updates">
            {waiting.map((p) => (
              <li key={p.title} className="list-row align-top">
                <span className="grow wrap-anywhere small">{p.title}</span>
                <span className="nowrap small">
                  <Badge tone={p.kind === "security" ? "warn" : p.kind === "feature" ? "info" : "muted"}>{UPDATE_KIND[p.kind] ?? p.kind}</Badge>
                  {p.size ? <span className="muted"> {formatBytes(p.size)}</span> : null}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
      <div className="row wrap security-buttons">
        <ActionButton disabled={busy("check_updates")} success="Asked the PC to check for updates" onClick={() => act("check_updates")}>
          Check now
        </ActionButton>
        <ActionButton
          variant="primary"
          disabled={busy("install_updates") || waiting.length === 0}
          success="Asked the PC to install the updates. It runs in the background."
          onClick={() => act("install_updates")}
        >
          Install now
        </ActionButton>
      </div>
    </Card>
  );
}

type SecurityForm = Pick<DeviceSettings, "updates_mode" | "feature_updates" | "restart_for_updates" | "weekly_full_scan" | "block_pause_updates">;

function SecuritySettings({ device }: { device: Device }) {
  const loaded = useLoader(() => api.settings(device.id), [device.id]);
  const [form, setForm] = useState<SecurityForm | null>(null);
  useEffect(() => {
    if (loaded.data) {
      const { updates_mode, feature_updates, restart_for_updates, weekly_full_scan, block_pause_updates } = loaded.data;
      setForm({ updates_mode, feature_updates, restart_for_updates, weekly_full_scan, block_pause_updates });
    }
  }, [loaded.data]);
  if (!form) return <Card title="Security settings">{loaded.error ? <ErrorNote error={loaded.error} /> : <Spinner />}</Card>;
  const d = loaded.data!;
  const dirty =
    form.updates_mode !== d.updates_mode ||
    form.feature_updates !== d.feature_updates ||
    form.restart_for_updates !== d.restart_for_updates ||
    form.weekly_full_scan !== d.weekly_full_scan ||
    form.block_pause_updates !== d.block_pause_updates;

  return (
    <Card title="Security settings">
      <div className="stack">
        <fieldset className="stack">
          <legend className="sr-only">Windows updates</legend>
          <label className="inline">
            <input type="radio" name="updates_mode" checked={form.updates_mode === "install"} onChange={() => setForm({ ...form, updates_mode: "install" })} />
            Install Windows updates automatically
          </label>
          <label className="inline">
            <input type="radio" name="updates_mode" checked={form.updates_mode === "report"} onChange={() => setForm({ ...form, updates_mode: "report" })} />
            Only show me what's waiting
          </label>
        </fieldset>
        <label className="inline">
          <input type="checkbox" checked={form.feature_updates} onChange={(e) => setForm({ ...form, feature_updates: e.target.checked })} />
          Also install new Windows versions (the big yearly upgrades)
        </label>
        <label className="inline align-start-check">
          <input type="checkbox" checked={form.restart_for_updates} onChange={(e) => setForm({ ...form, restart_for_updates: e.target.checked })} />
          <span>
            Restart at bedtime when an update needs it
            <span className="block muted small">After the bedtime lock has been on for 20 minutes. That evening's countdown tells your child to save their work.</span>
          </span>
        </label>
        <label className="inline align-start-check">
          <input type="checkbox" checked={form.block_pause_updates} onChange={(e) => setForm({ ...form, block_pause_updates: e.target.checked })} />
          <span>
            Stop my child pausing Windows updates
            <span className="block muted small">Hides "Pause updates" in Windows Settings on Windows Pro, Education and Enterprise. Windows Home doesn't allow it; you're alerted instead.</span>
          </span>
        </label>
        <label className="inline align-start-check">
          <input type="checkbox" checked={form.weekly_full_scan} onChange={(e) => setForm({ ...form, weekly_full_scan: e.target.checked })} />
          <span>
            Full virus scan every week, at bedtime
            <span className="block muted small">If this is off, a quick scan runs at bedtime when there's been no scan for a week.</span>
          </span>
        </label>
        <div className="form-actions">
          <ActionButton
            variant="primary"
            disabled={!dirty}
            success="Security settings saved"
            onClick={async () => {
              await api.patchSettings(device.id, form);
              await loaded.reload();
            }}
          >
            Save security settings
          </ActionButton>
        </div>
      </div>
    </Card>
  );
}
