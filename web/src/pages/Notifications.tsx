import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type NotificationLevel, type NotificationSettings } from "../api";
import { browserTimeZone, formatDateTime, relativeTime, timeZones } from "../format";
import { currentSubscription, disablePush, enablePush, pushSupport } from "../push";
import { ActionButton, Badge, Card, ErrorNote, Spinner, useLoader, useToast } from "../ui";

const LEVEL_LABELS: Record<NotificationLevel, string> = { immediate: "Right away", digest: "Daily summary", off: "Off" };

export default function Notifications() {
  const loaded = useLoader(() => api.notificationSettings(), []);
  const [form, setForm] = useState<NotificationSettings | null>(null);
  useEffect(() => {
    if (loaded.data) {
      // First visit: suggest this browser's time zone rather than UTC.
      const fresh = loaded.data.timezone === "UTC" && loaded.data.quiet_start === null;
      setForm(fresh ? { ...loaded.data, timezone: browserTimeZone() } : loaded.data);
    }
  }, [loaded.data]);

  return (
    <>
      <p className="crumbs">
        <Link to="/account">Account</Link> /
      </p>
      <div className="page-head">
        <h1>Notifications</h1>
      </div>
      <ErrorNote error={loaded.error} />
      {!form ? (
        <Spinner />
      ) : (
        <div className="columns">
          <div className="stack">
            <ChannelsCard form={form} setForm={setForm} onChange={loaded.reload} />
            <LevelsCard form={form} setForm={setForm} />
          </div>
          <div className="stack">
            <TimingCard form={form} setForm={setForm} />
            <SaveCard form={form} onSaved={loaded.reload} />
            <RecentCard />
          </div>
        </div>
      )}
    </>
  );
}

type FormProps = { form: NotificationSettings; setForm: (f: NotificationSettings) => void };

function ChannelsCard({ form, setForm, onChange }: FormProps & { onChange: () => Promise<void> }) {
  const support = pushSupport();
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  useEffect(() => {
    void currentSubscription().then((s) => setSubscribed(!!s));
  }, []);

  return (
    <Card title="How to reach you">
      <label className="inline switch">
        <input
          type="checkbox"
          disabled={!form.email_available}
          checked={form.email_enabled && form.email_available}
          onChange={(e) => setForm({ ...form, email_enabled: e.target.checked })}
        />
        Email me at {form.email_address}
      </label>
      {!form.email_available && (
        <p className="muted small">
          Email isn't set up on the server yet. Whoever hosts Kidputer Control can add an email service (see "Email notifications" in the
          Render guide). Push notifications work without it.
        </p>
      )}

      <label className="inline switch">
        <input type="checkbox" checked={form.push_enabled} onChange={(e) => setForm({ ...form, push_enabled: e.target.checked })} />
        Push notifications on my phone or computer
      </label>
      <p className="muted small">
        {form.push_devices === 0
          ? "No phone or browser is set up for push notifications yet."
          : `Push is set up on ${form.push_devices} ${form.push_devices === 1 ? "device" : "devices"}.`}
      </p>
      {support === "supported" ? (
        <div className="row wrap">
          {subscribed ? (
            <>
              <Badge tone="ok">On for this device</Badge>
              <ActionButton
                variant="ghost"
                success="Push notifications turned off for this device"
                onClick={async () => {
                  await disablePush();
                  setSubscribed(false);
                  await onChange();
                }}
              >
                Turn off for this device
              </ActionButton>
            </>
          ) : (
            <ActionButton
              variant="primary"
              success="Push notifications turned on for this device"
              onClick={async () => {
                await enablePush(form.vapid_public_key);
                setSubscribed(true);
                await onChange();
              }}
            >
              Turn on for this device
            </ActionButton>
          )}
        </div>
      ) : support === "needs-home-screen" ? (
        <p className="warning-note">
          On iPhone and iPad, add Kidputer Control to your Home Screen first: tap the Share button, then <strong>Add to Home Screen</strong>.
          Open it from there and come back to this page.
        </p>
      ) : (
        <p className="muted small">This browser can't receive push notifications. Try Chrome, Edge, Firefox, or Safari.</p>
      )}
    </Card>
  );
}

function LevelsCard({ form, setForm }: FormProps) {
  return (
    <Card title="What to tell you">
      <p className="muted small">
        <strong>Right away</strong> sends an alert within a minute; a burst of the same alert is grouped into one.{" "}
        <strong>Daily summary</strong> collects them into one message a day.
      </p>
      <ul className="list" data-testid="notification-levels">
        {form.categories.map((c) => (
          <li key={c.id} className="list-row">
            <div className="grow">
              <strong>{c.label}</strong>
              <div className="muted small">{c.description}</div>
            </div>
            <select
              className="level-select"
              aria-label={c.label}
              value={form.levels[c.id] ?? c.default}
              onChange={(e) => setForm({ ...form, levels: { ...form.levels, [c.id]: e.target.value as NotificationLevel } })}
            >
              {(Object.keys(LEVEL_LABELS) as NotificationLevel[]).map((l) => (
                <option key={l} value={l}>
                  {LEVEL_LABELS[l]}
                </option>
              ))}
            </select>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function TimingCard({ form, setForm }: FormProps) {
  const quiet = form.quiet_start !== null && form.quiet_end !== null;
  return (
    <Card title="Quiet hours and daily summary">
      <label className="inline switch">
        <input
          type="checkbox"
          checked={quiet}
          onChange={(e) => setForm({ ...form, quiet_start: e.target.checked ? "22:00" : null, quiet_end: e.target.checked ? "07:00" : null })}
        />
        Quiet hours
      </label>
      <p className="muted small">Alerts that arrive during quiet hours wait until they end.</p>
      {quiet && (
        <div className="fields">
          <label>
            From
            <input type="time" value={(form.quiet_start ?? "").slice(0, 5)} onChange={(e) => setForm({ ...form, quiet_start: e.target.value })} />
          </label>
          <label>
            Until
            <input type="time" value={(form.quiet_end ?? "").slice(0, 5)} onChange={(e) => setForm({ ...form, quiet_end: e.target.value })} />
          </label>
        </div>
      )}
      <div className="fields">
        <label>
          Send the daily summary at
          <select value={form.digest_hour} onChange={(e) => setForm({ ...form, digest_hour: Number(e.target.value) })}>
            {Array.from({ length: 24 }, (_, h) => (
              <option key={h} value={h}>
                {String(h).padStart(2, "0")}:00
              </option>
            ))}
          </select>
        </label>
        <label>
          Your time zone
          <select value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
            {timeZones().map((z) => (
              <option key={z}>{z}</option>
            ))}
          </select>
        </label>
      </div>
    </Card>
  );
}

function SaveCard({ form, onSaved }: { form: NotificationSettings; onSaved: () => Promise<void> }) {
  const toast = useToast();
  return (
    <Card>
      <div className="row wrap">
        <ActionButton
          variant="primary"
          success="Notification settings saved"
          onClick={async () => {
            await api.saveNotificationSettings({
              email_enabled: form.email_enabled,
              push_enabled: form.push_enabled,
              levels: form.levels,
              quiet_start: form.quiet_start,
              quiet_end: form.quiet_end,
              timezone: form.timezone,
              digest_hour: form.digest_hour,
            });
            await onSaved();
          }}
        >
          Save notification settings
        </ActionButton>
        <ActionButton
          onClick={async () => {
            const r = await api.testNotification();
            const parts = [];
            if (r.email) parts.push("an email");
            if (r.push) parts.push(`${r.push} push ${r.push === 1 ? "notification" : "notifications"}`);
            toast(parts.length ? `Sent ${parts.join(" and ")}.` : r.email_error ? `Email failed: ${r.email_error}` : "Nothing to send to yet.", parts.length ? "ok" : "danger");
          }}
        >
          Send a test
        </ActionButton>
      </div>
    </Card>
  );
}

function RecentCard() {
  const recent = useLoader(() => api.recentNotifications(), [], 30000);
  return (
    <Card title="Recent notifications">
      <ErrorNote error={recent.error} />
      {recent.data === undefined ? (
        <Spinner />
      ) : recent.data.length === 0 ? (
        <p className="muted">Nothing yet.</p>
      ) : (
        <ul className="list" data-testid="recent-notifications">
          {recent.data.map((n) => (
            <li key={n.id} className="list-row">
              <div className="grow">
                <strong>{n.title}</strong>
                <div className="muted small wrap-anywhere">{n.body}</div>
                <div className="muted small">
                  <time dateTime={n.created_at} title={formatDateTime(n.created_at)}>
                    {relativeTime(n.created_at)}
                  </time>{" "}
                  · {n.sent_at ? "sent" : n.level === "digest" ? "in the next daily summary" : "sending"}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
