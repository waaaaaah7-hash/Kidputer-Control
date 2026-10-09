import { useCallback, useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { api, ApiError, REAUTH_REQUIRED } from "./api";
import { UserCancelled } from "./cancel";
import { confirmWithPasskey, passkeysSupported } from "./passkeys";
import { ReauthContext, type ReauthRunner } from "./reauthContext";
import { Dialog, ErrorNote, errorMessage } from "./ui";

/**
 * Risky actions (delete a PC, change its PIN, download an installer, manage passkeys) need a recent
 * second-factor check. When the server says so, ask for a passkey or authenticator code, then retry.
 */
export function ReauthProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const run: ReauthRunner = useCallback(async (action) => {
    try {
      return await action();
    } catch (e) {
      if (!(e instanceof ApiError && e.code === REAUTH_REQUIRED)) throw e;
      const ok = await new Promise<boolean>((resolve) => {
        resolver.current = resolve;
        setOpen(true);
      });
      if (!ok) throw new UserCancelled();
      return await action();
    }
  }, []);

  const finish = (ok: boolean) => {
    setOpen(false);
    resolver.current?.(ok);
    resolver.current = null;
  };

  return (
    <ReauthContext.Provider value={run}>
      {children}
      <ReauthDialog open={open} onDone={finish} />
    </ReauthContext.Provider>
  );
}

function ReauthDialog({ open, onDone }: { open: boolean; onDone: (ok: boolean) => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hasPasskey, setHasPasskey] = useState(false);

  useEffect(() => {
    if (!open) return;
    setCode("");
    setError(null);
    let cancelled = false;
    if (passkeysSupported()) {
      api
        .passkeys()
        .then((list) => !cancelled && setHasPasskey(list.length > 0))
        .catch(() => {});
    }
    return () => {
      cancelled = true;
    };
  }, [open]);

  async function attempt(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onDone(true);
    } catch (e) {
      if (!(e instanceof UserCancelled)) setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }

  function submitCode(e: FormEvent) {
    e.preventDefault();
    void attempt(async () => {
      try {
        await api.reverify(code);
      } finally {
        setCode("");
      }
    });
  }

  return (
    <Dialog open={open} onClose={() => onDone(false)} title="Confirm it's you">
      <div className="stack">
        <p className="muted">This change affects your child's PC, so please confirm it's you.</p>
        {hasPasskey && (
          <button type="button" className="btn btn-primary btn-block" disabled={busy} onClick={() => void attempt(confirmWithPasskey)}>
            Use a passkey
          </button>
        )}
        <form className="stack" onSubmit={submitCode}>
          <label>
            {hasPasskey ? "Or enter the code from your authenticator app" : "Code from your authenticator app"}
            <input
              inputMode="numeric"
              autoComplete="one-time-code"
              pattern="\d{6}"
              maxLength={6}
              required
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              autoFocus={!hasPasskey}
            />
          </label>
          <ErrorNote error={error} />
          <div className="form-actions">
            <button type="button" className="btn btn-ghost" onClick={() => onDone(false)}>
              Cancel
            </button>
            <button type="submit" className={hasPasskey ? "btn btn-secondary" : "btn btn-primary"} disabled={busy}>
              Confirm
            </button>
          </div>
        </form>
      </div>
    </Dialog>
  );
}
