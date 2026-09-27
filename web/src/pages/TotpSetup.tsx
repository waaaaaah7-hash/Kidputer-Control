import QRCode from "qrcode";
import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, Spinner, errorMessage } from "../ui";

/** Mandatory two-factor enrollment right after the first password sign-in. */
export default function TotpSetup() {
  const { admin, loading, refresh, logout } = useAuth();
  const navigate = useNavigate();
  const [secret, setSecret] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const needsSetup = !!admin && !admin.totp_enabled;
  useEffect(() => {
    if (!needsSetup) return;
    let cancelled = false;
    api
      .totpSetup()
      .then(async (r) => {
        const url = await QRCode.toDataURL(r.otpauth_uri, { margin: 1, width: 220 });
        if (!cancelled) {
          setSecret(r.secret);
          setQr(url);
        }
      })
      .catch((e) => !cancelled && setError(errorMessage(e)));
    return () => {
      cancelled = true;
    };
  }, [needsSetup]);

  if (loading) return <Spinner />;
  if (!admin) return <Navigate to="/login" replace />;
  if (admin.totp_enabled) return <Navigate to="/" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.totpEnable(code);
      await refresh();
      navigate("/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <h1>Set up two-factor sign-in</h1>
        <p className="muted">
          The dashboard can lock and unlock your child's PC from anywhere, so it's protected by a second factor. Scan this code with an
          authenticator app (Google Authenticator, Microsoft Authenticator, 1Password, …).
        </p>
        {qr ? <img className="qr" src={qr} alt="QR code for your authenticator app" width={220} height={220} /> : <Spinner label="Generating code…" />}
        {secret && (
          <p className="muted small">
            Can't scan? Enter this key: <code className="secret">{secret.replace(/(.{4})/g, "$1 ").trim()}</code>
          </p>
        )}
        <label>
          Code from the app
          <input
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="\d{6}"
            maxLength={6}
            required
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          />
        </label>
        <ErrorNote error={error} />
        <button type="submit" className="btn btn-primary btn-block" disabled={busy || !secret}>
          Turn on two-factor sign-in
        </button>
        <button type="button" className="btn btn-ghost btn-block" onClick={() => void logout()}>
          Sign out
        </button>
      </form>
    </main>
  );
}
