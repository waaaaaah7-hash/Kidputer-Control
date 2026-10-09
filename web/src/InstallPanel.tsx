import { useState } from "react";
import { api, type DeviceMode, type EnrollmentCode } from "./api";
import EnrollmentInfo from "./EnrollmentInfo";
import { ActionButton } from "./ui";

/** Save a Blob as a file via a temporary object URL (what the browser does for a normal download link). */
function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/**
 * Installing the agent on the child's PC. The main route is the one-click installer: a zip with the
 * server address and a fresh enrollment code built in. Installing by hand (code + command) stays available.
 * Every download issues a new code, which replaces any code shown before.
 */
export default function InstallPanel({
  deviceId,
  enrolled,
  initialCode = null,
  initialMode = "managed",
  onChange,
}: {
  deviceId: string;
  enrolled: boolean;
  initialCode?: EnrollmentCode | null;
  initialMode?: DeviceMode;
  onChange?: () => Promise<void>;
}) {
  const [code, setCode] = useState<EnrollmentCode | null>(initialCode);
  const [childUser, setChildUser] = useState("");
  const [mode, setMode] = useState<DeviceMode>(initialMode);
  const [downloaded, setDownloaded] = useState(false);

  async function download() {
    const r = await api.downloadInstaller(deviceId, { child_user: childUser.trim() || undefined, replace: enrolled, mode });
    saveBlob(r.blob, r.filename);
    setCode(r.code);
    setDownloaded(true);
    await onChange?.();
  }

  return (
    <div className="stack install">
      <ModeChoice mode={mode} onChange={setMode} />
      <label>
        Child's Windows account (optional, the installer lets you pick it if left blank)
        <input maxLength={104} placeholder="e.g. Sam" value={childUser} onChange={(e) => setChildUser(e.target.value)} />
      </label>
      <div className="form-actions">
        <ActionButton
          variant={enrolled ? "secondary" : "primary"}
          confirm={enrolled ? "This PC is already installed. A new installer disconnects it until you run the new installer on it. Continue?" : undefined}
          success="Installer downloaded"
          onClick={download}
        >
          Download installer
        </ActionButton>
      </div>
      <ol className={downloaded ? "steps steps-done" : "steps"}>
        <li>Copy the downloaded zip to the child's PC and unzip it.</li>
        <li>
          Signed in as a parent (administrator), double-click <strong>Install Kidputer Control</strong> and click <strong>Yes</strong> when
          Windows asks for permission.
        </li>
        <li>Pick the child's account from the list. That's it.</li>
      </ol>
      <p className="muted small">
        The installer contains a one-time code that expires after 24 hours. If Windows says "Windows protected your PC", click{" "}
        <strong>More info</strong>, then <strong>Run anyway</strong>.
      </p>
      <details className="manual">
        <summary>Install by hand instead</summary>
        {code ? (
          <EnrollmentInfo code={code} childHint={childUser.trim()} />
        ) : (
          <div className="stack">
            <p className="muted small">Get an enrollment code to use with Install.ps1 from the repository's agent folder.</p>
            <div className="form-actions">
              <ActionButton
                confirm={enrolled ? "The PC will stop syncing until it's reinstalled with the new code. Continue?" : undefined}
                onClick={async () => {
                  setCode(await api.reissueEnrollmentCode(deviceId));
                  await onChange?.();
                }}
              >
                Show a new enrollment code
              </ActionButton>
            </div>
          </div>
        )}
      </details>
    </div>
  );
}

/** Managed or Trusted: what kind of Windows account the child uses, and what that means. */
export function ModeChoice({ mode, onChange }: { mode: DeviceMode; onChange: (m: DeviceMode) => void }) {
  return (
    <fieldset className="mode-choice stack">
      <legend>What kind of Windows account does your child use?</legend>
      <label className="inline">
        <input type="radio" name="device-mode" checked={mode === "managed"} onChange={() => onChange("managed")} />
        <span>
          <strong>Standard account (Managed)</strong>
          <span className="muted small block">
            Recommended. The child can't switch Kidputer Control off. Apps from the Microsoft Store install freely; other installs go
            through a request you approve.
          </span>
        </span>
      </label>
      <label className="inline">
        <input type="radio" name="device-mode" checked={mode === "trusted"} onChange={() => onChange("trusted")} />
        <span>
          <strong>Administrator account (Trusted)</strong>
          <span className="muted small block">
            The child installs anything themselves. The schedule still applies, but they could switch it off, so you get notified about
            installs and if the PC goes silent.
          </span>
        </span>
      </label>
    </fieldset>
  );
}
