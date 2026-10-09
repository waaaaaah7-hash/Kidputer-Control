import { useState, type FormEvent } from "react";
import { api, type Passkey } from "../api";
import { useAuth } from "../auth";
import { authMethodLabel, describeUserAgent, formatDateTime, relativeTime } from "../format";
import { addPasskey, passkeysSupported } from "../passkeys";
import { ActionButton, Badge, Card, ErrorNote, Spinner, errorMessage, useLoader, useToast } from "../ui";

export default function Account() {
  const { admin } = useAuth();
  return (
    <>
      <div className="page-head">
        <h1>Account</h1>
      </div>
      <div className="columns">
        <div className="stack">
          <PasskeysCard />
          <Card title="Two-factor sign-in">
            <p>
              Signed in as <strong>{admin?.email}</strong>. Your authenticator app is <strong>on</strong> and stays as a backup way in
              if you don't have your passkey with you.
            </p>
            <p className="muted small">
              Lost your authenticator? Whoever hosts the server can reset it with <code>python -m app.cli reset-totp {admin?.email}</code>.
            </p>
          </Card>
          <ChangePasswordCard />
        </div>
        <div className="stack">
          <SignedInDevicesCard />
          <RememberedDevicesCard />
        </div>
      </div>
    </>
  );
}

// --- Passkeys -----------------------------------------------------------------------------------------

function PasskeysCard() {
  const passkeys = useLoader(() => api.passkeys(), []);
  const [name, setName] = useState("");
  const supported = passkeysSupported();

  return (
    <Card title="Passkeys">
      <p className="muted small">
        Sign in with Face ID, your fingerprint, or Windows Hello instead of your password and code. A passkey counts as two-factor
        sign-in on its own, and it can't be phished.
      </p>
      <ErrorNote error={passkeys.error} />
      {passkeys.data === undefined ? (
        <Spinner />
      ) : passkeys.data.length === 0 ? (
        <p>You haven't added a passkey yet.</p>
      ) : (
        <ul className="list" data-testid="passkey-list">
          {passkeys.data.map((p) => (
            <PasskeyRow key={p.id} passkey={p} onChange={passkeys.reload} />
          ))}
        </ul>
      )}
      {supported ? (
        <div className="row wrap add-passkey">
          <input aria-label="Passkey name" placeholder="Name, e.g. My iPhone" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
          <ActionButton
            variant="primary"
            success="Passkey added"
            onClick={async () => {
              await addPasskey(name.trim());
              setName("");
              await passkeys.reload();
            }}
          >
            Add a passkey
          </ActionButton>
        </div>
      ) : (
        <p className="warning-note">This browser doesn't support passkeys. Try a recent version of Chrome, Edge, Safari, or Firefox.</p>
      )}
    </Card>
  );
}

function PasskeyRow({ passkey, onChange }: { passkey: Passkey; onChange: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(passkey.name);
  return (
    <li className="list-row">
      <div className="grow">
        {editing ? (
          <input aria-label="New passkey name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        ) : (
          <strong>{passkey.name}</strong>
        )}{" "}
        {passkey.backed_up && <Badge tone="info">Syncs across your devices</Badge>}
        <div className="muted small">
          Added {formatDateTime(passkey.created_at)} · {passkey.last_used_at ? `last used ${relativeTime(passkey.last_used_at)}` : "not used yet"}
        </div>
      </div>
      <div className="row">
        {editing ? (
          <ActionButton
            disabled={!name.trim()}
            onClick={async () => {
              await api.renamePasskey(passkey.id, name.trim());
              setEditing(false);
              await onChange();
            }}
          >
            Save
          </ActionButton>
        ) : (
          <button type="button" className="btn btn-ghost" onClick={() => setEditing(true)}>
            Rename
          </button>
        )}
        <ActionButton
          variant="ghost"
          confirm={`Remove the passkey "${passkey.name}"? You can still sign in with your password and code.`}
          success="Passkey removed"
          onClick={async () => {
            await api.removePasskey(passkey.id);
            await onChange();
          }}
        >
          Remove
        </ActionButton>
      </div>
    </li>
  );
}

// --- Signed-in and remembered devices -------------------------------------------------------------------

function SignedInDevicesCard() {
  const sessions = useLoader(() => api.sessions(), []);
  const others = (sessions.data ?? []).filter((s) => !s.current).length;
  return (
    <Card title="Signed-in devices">
      <p className="muted small">Everywhere you're signed in right now. Sign out anything you don't recognise, such as a lost phone.</p>
      <ErrorNote error={sessions.error} />
      {sessions.data === undefined ? (
        <Spinner />
      ) : (
        <ul className="list" data-testid="session-list">
          {sessions.data.map((s) => (
            <li key={s.id} className="list-row">
              <div className="grow">
                <strong>{describeUserAgent(s.user_agent)}</strong> {s.current && <Badge tone="ok">This device</Badge>}
                <div className="muted small">
                  {authMethodLabel(s.auth_method)} · active {relativeTime(s.last_seen_at ?? s.created_at)}
                  {s.ip ? ` · ${s.ip}` : ""}
                </div>
              </div>
              {!s.current && (
                <ActionButton
                  variant="ghost"
                  success="Signed out"
                  onClick={async () => {
                    await api.signOutSession(s.id);
                    await sessions.reload();
                  }}
                >
                  Sign out
                </ActionButton>
              )}
            </li>
          ))}
        </ul>
      )}
      {others > 0 && (
        <div className="form-actions">
          <ActionButton
            confirm="Sign out everywhere except this device?"
            success="Signed out of other devices"
            onClick={async () => {
              await api.signOutOthers();
              await sessions.reload();
            }}
          >
            Sign out all other devices
          </ActionButton>
        </div>
      )}
    </Card>
  );
}

function RememberedDevicesCard() {
  const devices = useLoader(() => api.rememberedDevices(), []);
  return (
    <Card title="Remembered devices">
      <p className="muted small">
        Browsers where you ticked "Remember this device": signing in there needs only your password. Risky changes still ask you to
        confirm it's you.
      </p>
      <ErrorNote error={devices.error} />
      {devices.data === undefined ? (
        <Spinner />
      ) : devices.data.length === 0 ? (
        <p className="muted">No remembered devices.</p>
      ) : (
        <ul className="list" data-testid="remembered-list">
          {devices.data.map((d) => (
            <li key={d.id} className="list-row">
              <div className="grow">
                <strong>{describeUserAgent(d.user_agent)}</strong> {d.current && <Badge tone="ok">This browser</Badge>}
                <div className="muted small">
                  Remembered until {formatDateTime(d.expires_at)}
                  {d.last_used_at ? ` · last used ${relativeTime(d.last_used_at)}` : ""}
                </div>
              </div>
              <ActionButton
                variant="ghost"
                confirm="Forget this device? It will be signed out, and need the code at its next sign-in."
                success="Device forgotten"
                onClick={async () => {
                  await api.forgetDevice(d.id);
                  await devices.reload();
                }}
              >
                Forget
              </ActionButton>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// --- Password ----------------------------------------------------------------------------------------

function ChangePasswordCard() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [next2, setNext2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (next !== next2) return setError("The new passwords don't match.");
    try {
      await api.changePassword(current, next);
      setCurrent("");
      setNext("");
      setNext2("");
      toast("Password changed. Other devices were signed out and forgotten.");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <Card title="Change password">
      <form className="stack" onSubmit={submit}>
        <label>
          Current password
          <input type="password" autoComplete="current-password" required value={current} onChange={(e) => setCurrent(e.target.value)} />
        </label>
        <label>
          New password (at least 12 characters)
          <input type="password" autoComplete="new-password" minLength={12} required value={next} onChange={(e) => setNext(e.target.value)} />
        </label>
        <label>
          Repeat new password
          <input type="password" autoComplete="new-password" minLength={12} required value={next2} onChange={(e) => setNext2(e.target.value)} />
        </label>
        <ErrorNote error={error} />
        <div className="form-actions">
          <button type="submit" className="btn btn-primary">
            Change password
          </button>
        </div>
      </form>
    </Card>
  );
}
