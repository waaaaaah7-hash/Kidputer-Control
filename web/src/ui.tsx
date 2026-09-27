import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "./api";
import type { Tone } from "./format";

// --- Data loading ---------------------------------------------------------------------------------

export interface Loaded<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => Promise<void>;
}

/** Load data on mount / when deps change, optionally re-polling in the background. */
export function useLoader<T>(fn: () => Promise<T>, deps: unknown[], pollMs?: number): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  const reload = useCallback(async () => {
    try {
      setData(await fnRef.current());
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    void reload();
    if (!pollMs) return;
    const t = window.setInterval(() => {
      if (document.visibilityState === "visible") void reload();
    }, pollMs);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { data, error, loading, reload };
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

// --- Toasts ---------------------------------------------------------------------------------------

interface Toast {
  id: number;
  text: string;
  tone: Tone;
}

const ToastContext = createContext<(text: string, tone?: Tone) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const show = useCallback((text: string, tone: Tone = "ok") => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, text, tone }]);
    window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), tone === "danger" ? 7000 : 3500);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast tone-${t.tone}`}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

// --- Components -----------------------------------------------------------------------------------

type ButtonVariant = "primary" | "secondary" | "danger" | "ghost";

/**
 * Button that runs an async action, shows a busy state, and reports failures as a toast.
 * `success` is shown as a toast when the action resolves.
 */
export function ActionButton(props: {
  onClick: () => Promise<unknown>;
  children: ReactNode;
  variant?: ButtonVariant;
  success?: string;
  confirm?: string;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const { onClick, children, variant = "secondary", success, confirm, disabled, type = "button" } = props;
  const [busy, setBusy] = useState(false);
  const toast = useToast();
  return (
    <button
      type={type}
      className={`btn btn-${variant}`}
      disabled={disabled || busy}
      aria-busy={busy}
      onClick={async () => {
        if (confirm && !window.confirm(confirm)) return;
        setBusy(true);
        try {
          await onClick();
          if (success) toast(success);
        } catch (e) {
          toast(errorMessage(e), "danger");
        } finally {
          setBusy(false);
        }
      }}
    >
      {children}
    </button>
  );
}

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge tone-${tone}`}>{children}</span>;
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function ErrorNote({ error }: { error: string | null | undefined }) {
  if (!error) return null;
  return (
    <p className="error-note" role="alert">
      {error}
    </p>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <p className="muted" aria-busy="true">
      {label}
    </p>
  );
}

/** Modal built on <dialog> (focus trap and Esc handling come from the browser). */
export function Dialog({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal?.();
    if (!open && el.open) el.close?.();
  }, [open]);
  return (
    <dialog ref={ref} className="dialog" onClose={onClose} aria-label={title}>
      {open && (
        <>
          <header className="card-head">
            <h2>{title}</h2>
            <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </header>
          {children}
        </>
      )}
    </dialog>
  );
}
