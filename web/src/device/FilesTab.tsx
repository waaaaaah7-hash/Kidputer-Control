import { useEffect, useState } from "react";
import { api, type Device, type DeviceCommand, type DriveInfo, type ScanNode, type ScanResult } from "../api";
import { formatBytes, formatDateTime, relativeTime } from "../format";
import { useLiveDevice } from "../live";
import { ActionButton, Badge, Card, ErrorNote, Spinner, useLoader } from "../ui";

const TYPE_LABELS: Record<string, string> = {
  video: "Videos",
  music: "Music",
  pictures: "Pictures",
  archives: "Archives (zip, rar, 7z, iso)",
  programs: "Programs",
  "game data": "Game data",
  documents: "Documents",
  other: "Other",
};

/**
 * The PC's drives, folder sizes (like TreeSize) and a file explorer (roadmap #19).
 * Names, sizes and dates only: files can't be opened or downloaded from here.
 */
export default function FilesTab({ device }: { device: Device }) {
  useLiveDevice(device.id);
  const drives = useLoader(() => api.drives(device.id), [device.id], 10000);
  const [letter, setLetter] = useState<string | null>(null);

  const list = drives.data?.drives ?? [];
  const selected = letter ?? Object.keys(drives.data?.scans ?? {})[0] ?? null;

  return (
    <div className="stack">
      <Card title="Drives" actions={drives.data?.updated_at ? <span className="muted small">updated {relativeTime(drives.data.updated_at)}</span> : undefined}>
        <ErrorNote error={drives.error} />
        {drives.data === undefined ? (
          <Spinner />
        ) : list.length === 0 ? (
          <p className="muted">Waiting for the PC to report its drives.</p>
        ) : (
          <ul className="drives" data-testid="drives">
            {list.map((d) => (
              <DriveRow
                key={d.name}
                device={device}
                drive={d}
                scan={drives.data!.scans[d.name.slice(0, 1).toUpperCase()]}
                scanning={drives.data!.scanning}
                selected={selected === d.name.slice(0, 1).toUpperCase()}
                onSelect={() => setLetter(d.name.slice(0, 1).toUpperCase())}
                onChange={drives.reload}
              />
            ))}
          </ul>
        )}
        {(drives.data?.devices.length ?? 0) > 0 && (
          <p className="small">
            <strong>Connected devices:</strong> {drives.data!.devices.join(", ")}
          </p>
        )}
        <p className="muted small">Names, sizes and dates only: files can't be opened or downloaded from here.</p>
      </Card>
      {selected && drives.data?.scans[selected] && <ScanView device={device} letter={selected} scannedAt={String(drives.data.scans[selected]!.scanned_at)} />}
    </div>
  );
}

function DriveRow({
  device,
  drive,
  scan,
  scanning,
  selected,
  onSelect,
  onChange,
}: {
  device: Device;
  drive: DriveInfo;
  scan?: { scanned_at: string };
  scanning: { drive: string; files: number; started_at: string } | null;
  selected: boolean;
  onSelect: () => void;
  onChange: () => Promise<void>;
}) {
  const letter = drive.name.slice(0, 1).toUpperCase();
  const used = Math.max(0, drive.size - drive.free);
  const pct = drive.size ? Math.round((used / drive.size) * 100) : 0;
  const busy = scanning?.drive === letter;
  return (
    <li className={selected ? "drive selected" : "drive"}>
      <div className="drive-head">
        <strong>
          {letter}: {drive.label || (drive.type === "Fixed" ? "Local disk" : drive.type)}
        </strong>{" "}
        {drive.type !== "Fixed" && <Badge tone="info">{drive.type === "Removable" ? "USB" : drive.type}</Badge>}
      </div>
      <span className="bar-track" aria-hidden="true">
        <span className={pct >= 90 ? "bar-fill danger" : "bar-fill"} style={{ width: `${pct}%` }} />
      </span>
      <div className="muted small">
        {formatBytes(drive.free)} free of {formatBytes(drive.size)}
        {scan ? ` · scanned ${relativeTime(scan.scanned_at)}` : " · not scanned yet"}
        {busy ? ` · scanning… ${scanning!.files.toLocaleString()} files so far` : ""}
      </div>
      <div className="row wrap">
        {scan && (
          <button type="button" className="btn btn-ghost" onClick={onSelect} aria-pressed={selected}>
            Folder sizes
          </button>
        )}
        <ActionButton
          variant={scan ? "ghost" : "secondary"}
          disabled={busy}
          success={`Scan of ${letter}: requested. It runs in the background and takes a few minutes.`}
          onClick={async () => {
            await api.startScan(device.id, letter);
            await onChange();
          }}
        >
          {busy ? "Scanning…" : scan ? "Rescan" : "Scan for folder sizes"}
        </ActionButton>
      </div>
    </li>
  );
}

// --- Folder sizes (like TreeSize) -----------------------------------------------------------------------------

function ScanView({ device, letter, scannedAt }: { device: Device; letter: string; scannedAt: string }) {
  const scan = useLoader(() => api.scan(device.id, letter), [device.id, letter, scannedAt]);
  const [trail, setTrail] = useState<string[]>([]);
  useEffect(() => setTrail([]), [letter, scannedAt]);

  if (scan.data === undefined) return <Card title={`Drive ${letter}:`}>{scan.error ? <ErrorNote error={scan.error} /> : <Spinner />}</Card>;
  const s: ScanResult = scan.data;
  let node: ScanNode = s.scan.tree;
  for (const name of trail) node = node.c?.find((c) => c.n === name) ?? node;
  const path = [s.scan.tree.n.replace(/\\$/, ""), ...trail].join("\\") + (trail.length ? "" : "\\");
  const children = node.c ?? [];
  const shown = children.reduce((sum, c) => sum + c.s, 0);
  const filesHere = node.s - shown;

  return (
    <div className="columns">
      <div className="stack">
        <Card title="Folder sizes" actions={<span className="muted small">scanned {formatDateTime(s.scanned_at)}</span>}>
          <nav className="crumbs" aria-label="Folder">
            <button type="button" className="link-button" onClick={() => setTrail([])}>
              {letter}:
            </button>
            {trail.map((name, i) => (
              <span key={i}>
                {" \\ "}
                <button type="button" className="link-button" onClick={() => setTrail(trail.slice(0, i + 1))}>
                  {name}
                </button>
              </span>
            ))}
          </nav>
          <p className="small">
            <strong>{formatBytes(node.s)}</strong> in {node.f.toLocaleString()} files
            {node.a < node.s * 0.9 ? <span className="muted"> · {formatBytes(node.a)} on disk (some files are online-only)</span> : null}
          </p>
          <ul className="bars folders" data-testid="folders">
            {children.map((c) => (
              <li key={c.n}>
                <button type="button" className="bar-row" onClick={() => setTrail([...trail, c.n])} disabled={!c.c?.length && !c.x}>
                  <span className="bar-label">
                    {c.n}
                    {c.x ? " (not accessible)" : ""}
                  </span>
                  <span className="bar-track">
                    <span className="bar-fill" style={{ width: `${node.s ? Math.max(1, Math.round((c.s / node.s) * 100)) : 0}%` }} />
                  </span>
                  <span className="bar-value">{formatBytes(c.s)}</span>
                </button>
              </li>
            ))}
            {filesHere > 0 && children.length > 0 && (
              <li className="bar-row muted">
                <span className="bar-label">Files here and small folders</span>
                <span className="bar-track">
                  <span className="bar-fill muted-fill" style={{ width: `${Math.max(1, Math.round((filesHere / node.s) * 100))}%` }} />
                </span>
                <span className="bar-value">{formatBytes(filesHere)}</span>
              </li>
            )}
          </ul>
          <LiveFolder device={device} path={path} />
        </Card>
      </div>
      <div className="stack">
        <ChangesCard scan={s} />
        <LargestCard scan={s} />
        <TypesCard scan={s} />
      </div>
    </div>
  );
}

/** Ask the PC for the folder's actual contents right now (names, sizes, dates). */
function LiveFolder({ device, path }: { device: Device; path: string }) {
  const [cmd, setCmd] = useState<DeviceCommand | null>(null);
  useEffect(() => setCmd(null), [path]);
  useEffect(() => {
    if (!cmd || cmd.status !== "pending") return;
    const t = window.setTimeout(async () => setCmd(await api.command(device.id, cmd.id)), 1500);
    return () => window.clearTimeout(t);
  }, [cmd, device.id]);

  const result = cmd?.status === "done" ? (cmd.result as unknown as FolderListing) : null;
  return (
    <div className="live-folder">
      {!cmd ? (
        <ActionButton onClick={async () => setCmd(await api.listFolder(device.id, path))}>Show what's in this folder now</ActionButton>
      ) : cmd.status === "pending" ? (
        <p className="muted small">Asking the PC…</p>
      ) : cmd.status !== "done" ? (
        <p className="warning-note">{String(cmd.result?.message ?? "The PC didn't answer. Is it on?")}</p>
      ) : (
        <>
          <h3>In {result!.path}</h3>
          {result!.entries.length === 0 ? (
            <p className="muted">Empty.</p>
          ) : (
            <ul className="list" data-testid="folder-listing">
              {result!.entries.map((e) => (
                <li key={e.n} className="list-row">
                  <div className="grow wrap-anywhere">
                    {e.dir ? "📁 " : ""}
                    {e.n}
                    {e.h ? <span className="muted small"> · hidden</span> : null}
                  </div>
                  <span className="muted small nowrap">
                    {e.dir ? "" : `${formatBytes(e.s ?? 0)} · `}
                    {relativeTime(e.m)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {result!.truncated && <p className="muted small">Showing the first {result!.entries.length} of {result!.total}.</p>}
        </>
      )}
    </div>
  );
}

interface FolderListing {
  path: string;
  entries: { n: string; dir: boolean; s?: number; m: string; h: boolean; l: boolean }[];
  total: number;
  truncated: boolean;
}

function ChangesCard({ scan }: { scan: ScanResult }) {
  return (
    <Card title="What changed">
      {!scan.previous_scanned_at ? (
        <p className="muted">Shown after the next scan, compared with this one.</p>
      ) : scan.changes.length === 0 ? (
        <p className="muted">No folder grew by more than 50 MB since {formatDateTime(scan.previous_scanned_at)}.</p>
      ) : (
        <>
          <p className="muted small">Since {formatDateTime(scan.previous_scanned_at)}:</p>
          <ul className="list" data-testid="changes">
            {scan.changes.map((c) => (
              <li key={c.path} className="list-row">
                <span className="grow wrap-anywhere">{c.path}</span>
                <span className="small nowrap">{c.previous === null ? `new, ${formatBytes(c.size)}` : `+${formatBytes(c.size - c.previous)}`}</span>
              </li>
            ))}
          </ul>
        </>
      )}
    </Card>
  );
}

function LargestCard({ scan }: { scan: ScanResult }) {
  const [all, setAll] = useState(false);
  const files = scan.scan.largest ?? [];
  return (
    <Card title="Largest files">
      <ul className="list" data-testid="largest">
        {files.slice(0, all ? 100 : 10).map((f) => (
          <li key={f.p} className="list-row">
            <span className="grow wrap-anywhere small">{f.p}</span>
            <span className="small nowrap">{formatBytes(f.s)}</span>
          </li>
        ))}
      </ul>
      {files.length > 10 && (
        <button type="button" className="btn btn-ghost" onClick={() => setAll(!all)}>
          {all ? "Show fewer" : `Show all ${files.length}`}
        </button>
      )}
    </Card>
  );
}

function TypesCard({ scan }: { scan: ScanResult }) {
  const types = Object.entries(scan.scan.by_type ?? {}).sort((a, b) => b[1].s - a[1].s);
  const max = types[0]?.[1].s ?? 1;
  return (
    <Card title="Space by type">
      <ul className="bars" data-testid="by-type">
        {types.map(([k, v]) => (
          <li key={k} className="bar-row">
            <span className="bar-label">{TYPE_LABELS[k] ?? k}</span>
            <span className="bar-track">
              <span className="bar-fill" style={{ width: `${Math.max(1, Math.round((v.s / max) * 100))}%` }} />
            </span>
            <span className="bar-value">{formatBytes(v.s)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}
