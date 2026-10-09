import { useState, type FormEvent } from "react";
import { api, type Parent, type Passkey } from "../api";
import { useAuth } from "../auth";
import { UserCancelled } from "../cancel";
import { authMethodLabel, describeUserAgent, formatDateTime, relativeTime } from "../format";
import { addPasskey, passkeysSupported } from "../passkeys";
import { useReauth } from "../reauthContext";
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
          <YourNameCard />
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
          <ParentsCard />
          <SignedInDevicesCard />
          <RememberedDevicesCard />
        </div>
      </div>
    </>
  );
}

// --- Your name and the other parents --------------------------------------------------------------------

function YourNameCard() {
  const { admin, refresh } = useAuth();
  const [name, setName] = useState(admin?.display_name ?? "");
  const changed = name.trim() !== (admin?.display_name ?? "");
  return (
    <Card title="Your name">
      <p className="muted small">Shown to the other parents, and next to everything you do in the activity log.</p>
      <div className="row wrap add-passkey">
        <input aria-label="Your name" placeholder={admin?.name} maxLength={60} value={name} onChange={(e) => setName(e.target.value)} />
        <ActionButton
          variant="primary"
          success="Name saved"
          disabled={!name.trim() || !changed}
          onClick={async () => {
            await api.updateMe(name.trim());
            await refresh();
          }}
        >
          Save
        </ActionButton>
      </div>
    </Card>
  );
}

function ParentsCard() {
  const { admin } = useAuth();
  // Reload when your own name changes, so your row shows it.
  const parents = useLoader(() => api.parents(), [admin?.name]);
  const invites = useLoader(() => api.invites(), []);
  const [email, setEmail] = useState("");
  const [link, setLink] = useState<{ email: string; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  const reauth = useReauth();

  async function invite(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await reauth(() => api.createInvite(email.trim()));
      setLink({ email: r.invite.email, url: r.link });
      setEmail("");
      await invites.reload();
    } catch (err) {
      if (!(err instanceof UserCancelled)) toast(errorMessage(err), "danger");
    } finally {
      setBusy(false);
    }
  }

  async function copy(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copied");
    } catch {
      toast("Couldn't copy. Select the link and copy it yourself.", "danger");
    }
  }

  return (
    <Card title="Parents">
      <p className="muted small">Everyone here can see and change every PC. Each parent signs in with their own password and two-factor sign-in.</p>
      <ErrorNote error={parents.error ?? invites.error} />
      {parents.data === undefined ? (
        <Spinner />
      ) : (
        <ul className="list" data-testid="parent-list">
          {parents.data.map((p) => (
            <ParentRow key={p.id} parent={p} onChange={parents.reload} />
          ))}
        </ul>
      )}
      {!!invites.data?.length && (
        <>
          <h3>Waiting to join</h3>
          <ul className="list" data-testid="invite-list">
            {invites.data.map((i) => (
              <li key={i.id} className="list-row">
                <div className="grow">
                  <strong className="wrap-anywhere">{i.email}</strong>
                  <div className="muted small">
                    Invited {relativeTime(i.created_at)}
                    {i.invited_by ? ` by ${i.invited_by}` : ""} · link expires {formatDateTime(i.expires_at)}
                  </div>
                </div>
                <ActionButton
                  variant="ghost"
                  success="Invite cancelled"
                  onClick={async () => {
                    await api.cancelInvite(i.id);
                    if (link?.email === i.email) setLink(null);
                    await invites.reload();
                  }}
                >
                  Cancel
                </ActionButton>
              </li>
            ))}
          </ul>
        </>
      )}
      {link && (
        <div className="invite-link stack" data-testid="invite-link">
          <p className="small">
            Send this link to <strong className="wrap-anywhere">{link.email}</strong> yourself, by text or email. It works once and expires in 48
            hours. Anyone with it can join, so don't post it anywhere public.
          </p>
          <code className="secret">{link.url}</code>
          <div className="row wrap">
            <button type="button" className="btn btn-primary" onClick={() => void copy(link.url)}>
              Copy link
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setLink(null)}>
              Done
            </button>
          </div>
        </div>
      )}
      <form className="row wrap add-passkey" onSubmit={invite}>
        <input
          type="email"
          aria-label="Email of the parent to invite"
          placeholder="Their email address"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <button type="submit" className="btn btn-secondary" disabled={busy} aria-busy={busy}>
          Invite a parent
        </button>
      </form>
    </Card>
  );
}

function ParentRow({ parent, onChange }: { parent: Parent; onChange: () => Promise<void> }) {
  return (
    <li className="list-row">
      <div className="grow">
        <strong>{parent.name}</strong> {parent.you && <Badge tone="ok">You</Badge>}
        {!parent.totp_enabled && <Badge tone="warn">Still setting up</Badge>}
        <div className="muted small wrap-anywhere">
          {parent.email} · {parent.last_active_at ? `active ${relativeTime(parent.last_active_at)}` : "not signed in yet"}
          {parent.passkeys > 0 ? ` · ${parent.passkeys} passkey${parent.passkeys === 1 ? "" : "s"}` : ""}
        </div>
      </div>
      {!parent.you && (
        <ActionButton
          variant="ghost"
          confirm={`Remove ${parent.name} as a parent? They'll be signed out everywhere and won't be able to sign in again. The activity log keeps their name.`}
          success={`${parent.name} removed`}
          onClick={async () => {
            await api.removeParent(parent.id);
            await onChange();
          }}
        >
          Remove
        </ActionButton>
      )}
    </li>
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
