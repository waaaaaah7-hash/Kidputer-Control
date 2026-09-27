import { useState, type FormEvent } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { Card, ErrorNote, errorMessage, useToast } from "../ui";

export default function Account() {
  const { admin } = useAuth();
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
      toast("Password changed. Other sessions were signed out.");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  return (
    <>
      <div className="page-head">
        <h1>Account</h1>
      </div>
      <div className="columns">
        <Card title="Sign-in">
          <p>
            Signed in as <strong>{admin?.email}</strong>. Two-factor sign-in is <strong>on</strong>.
          </p>
          <p className="muted small">
            Lost your authenticator? Whoever hosts the server can reset it with <code>python -m app.cli reset-totp {admin?.email}</code>.
          </p>
        </Card>
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
      </div>
    </>
  );
}
