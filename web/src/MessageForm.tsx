import { useState, type FormEvent } from "react";
import { api, type Message } from "./api";
import { errorMessage, useToast } from "./ui";

/** Write a message for the PC's screen. Used on the PC's page and in the "Message" dialog on the home page. */
export default function MessageForm({
  deviceId,
  replacing = false,
  autoFocus = false,
  onSent,
}: {
  deviceId: string;
  replacing?: boolean;
  autoFocus?: boolean;
  onSent: () => Promise<void> | void;
}) {
  const [body, setBody] = useState("");
  const [mode, setMode] = useState<Message["mode"]>("closable");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  async function send(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.sendMessage(deviceId, body.trim(), mode);
      setBody("");
      toast("Message sent");
      await onSent();
    } catch (err) {
      toast(errorMessage(err), "danger");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={send}>
      <label>
        {replacing ? "Replace with a new message" : "Send a message"}
        <textarea
          id={`message-${deviceId}`}
          required
          maxLength={2000}
          rows={3}
          value={body}
          autoFocus={autoFocus}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Dinner's ready — time to wrap up!"
        />
      </label>
      <fieldset className="row wrap">
        <legend className="sr-only">Message type</legend>
        <label className="inline">
          <input type="radio" name={`mode-${deviceId}`} checked={mode === "closable"} onChange={() => setMode("closable")} /> Child can close it
        </label>
        <label className="inline">
          <input type="radio" name={`mode-${deviceId}`} checked={mode === "persistent"} onChange={() => setMode("persistent")} /> Stays until I remove it
        </label>
      </fieldset>
      <div className="form-actions">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          Send
        </button>
      </div>
    </form>
  );
}
