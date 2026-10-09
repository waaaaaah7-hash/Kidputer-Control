import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type InviteInfo } from "../api";
import { useAuth } from "../auth";
import { formatDateTime } from "../format";
import { ErrorNote, Spinner, errorMessage } from "../ui";

/**
 * Where an invite link lands. The token is after the `#`, so browsers never send it to the server
 * in the address or log it; we post it in the body instead.
 */
export default function Invite() {
  const { refresh } = useAuth();
  const navigate = useNavigate();
  const [token] = useState(() => window.location.hash.slice(1));
  const [info, setInfo] = useState<InviteInfo | null>(null);
  const [gone, setGone] = useState<string | null>(token ? null : "This invite link is incomplete. Copy the whole link and try again.");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    api
      .lookupInvite(token)
      .then((r) => !cancelled && setInfo(r))
      .catch((e) => !cancelled && setGone(errorMessage(e)));
    return () => {
      cancelled = true;
    };
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== password2) return setError("The passwords don't match.");
    setBusy(true);
    try {
      await api.acceptInvite(token, name.trim(), password);
      // Drop the token from the address bar and history now that it's used.
      window.history.replaceState(null, "", "/invite");
      await refresh();
      navigate("/setup-2fa", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand-mark" aria-hidden="true" />
        <h1>Join Kidputer Control</h1>
        {gone ? (
          <>
            <p className="warning-note">{gone}</p>
            <Link to="/login" className="btn btn-secondary btn-block">
              Go to sign in
            </Link>
          </>
        ) : !info ? (
          <Spinner />
        ) : (
          <>
            <p className="muted">
              {info.invited_by ?? "A parent"} invited <strong>{info.email}</strong> to help manage the family's PCs. This link works once
              and expires {formatDateTime(info.expires_at)}.
            </p>
            <label>
              Your name (shown to the other parents)
              <input autoComplete="name" required maxLength={60} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
            </label>
            <input type="email" autoComplete="username" value={info.email} readOnly hidden />
            <label>
              Choose a password (at least 12 characters)
              <input type="password" autoComplete="new-password" minLength={12} required value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
            <label>
              Repeat password
              <input type="password" autoComplete="new-password" minLength={12} required value={password2} onChange={(e) => setPassword2(e.target.value)} />
            </label>
            <p className="muted small">Next you'll set up an authenticator app, and can add a passkey after that.</p>
            <ErrorNote error={error} />
            <button type="submit" className="btn btn-primary btn-block" disabled={busy || !name.trim()}>
              Create my account
            </button>
          </>
        )}
      </form>
    </main>
  );
}
