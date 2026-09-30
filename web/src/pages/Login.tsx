import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, errorMessage } from "../ui";

export default function Login() {
  const { admin, refresh } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [step, setStep] = useState<"password" | "code">("password");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (admin?.totp_enabled) return <Navigate to={from} replace />;

  async function finish() {
    const me = await refresh();
    navigate(me && !me.totp_enabled ? "/setup-2fa" : from, { replace: true });
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (step === "password") {
        const r = await api.login(email, password);
        setPassword("");
        if (r.mfa_required) setStep("code");
        else await finish();
      } else {
        await api.loginMfa(code);
        await finish();
      }
    } catch (err) {
      setError(errorMessage(err));
      if (step === "code") setCode("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <div className="brand-mark" aria-hidden="true" />
        <h1>Kidputer Control</h1>
        {step === "password" ? (
          <>
            <p className="muted">Sign in to manage your family's PCs.</p>
            <label>
              Email
              <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
            </label>
            <label>
              Password
              <input type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </label>
          </>
        ) : (
          <>
            <p className="muted">Enter the 6-digit code from your authenticator app.</p>
            <label>
              Authentication code
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                pattern="\d{6}"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                autoFocus
              />
            </label>
          </>
        )}
        <ErrorNote error={error} />
        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {step === "password" ? "Continue" : "Verify"}
        </button>
        {step === "code" && (
          <button type="button" className="btn btn-ghost btn-block" onClick={() => { setStep("password"); setError(null); }}>
            Use a different account
          </button>
        )}
      </form>
    </main>
  );
}
