import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type ActivityEvent, type EventQuery } from "../api";
import EventList from "../EventList";
import { Card, ErrorNote, Spinner, errorMessage, useLoader } from "../ui";

const PAGE = 50;

export default function Activity() {
  const [params, setParams] = useSearchParams();
  const filters: EventQuery = {
    device_id: params.get("device") || undefined,
    severity: (params.get("severity") as EventQuery["severity"]) || undefined,
    source: (params.get("source") as EventQuery["source"]) || undefined,
  };
  const devices = useLoader(() => api.devices(), []);
  const [events, setEvents] = useState<ActivityEvent[] | null>(null);
  const [more, setMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const key = params.toString();

  useEffect(() => {
    let cancelled = false;
    setEvents(null);
    api
      .events({ ...filters, limit: PAGE })
      .then((page) => {
        if (cancelled) return;
        setEvents(page);
        setMore(page.length === PAGE);
      })
      .catch((e) => !cancelled && setError(errorMessage(e)));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  async function loadMore() {
    const last = events?.[events.length - 1];
    if (!last) return;
    try {
      const page = await api.events({ ...filters, limit: PAGE, before_id: last.id });
      setEvents([...(events ?? []), ...page]);
      setMore(page.length === PAGE);
    } catch (e) {
      setError(errorMessage(e));
    }
  }

  const set = (name: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  };

  return (
    <>
      <div className="page-head">
        <h1>Activity</h1>
      </div>
      <Card>
        <div className="row wrap filters">
          <label>
            Device
            <select value={filters.device_id ?? ""} onChange={(e) => set("device", e.target.value)}>
              <option value="">All devices</option>
              {devices.data?.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Importance
            <select value={filters.severity ?? ""} onChange={(e) => set("severity", e.target.value)}>
              <option value="">Everything</option>
              <option value="warning">Warnings</option>
              <option value="critical">Critical</option>
            </select>
          </label>
          <label>
            From
            <select value={filters.source ?? ""} onChange={(e) => set("source", e.target.value)}>
              <option value="">PCs and parents</option>
              <option value="agent">PCs</option>
              <option value="admin">Parents</option>
            </select>
          </label>
        </div>
        <ErrorNote error={error} />
        {events ? <EventList events={events} devices={devices.data} showDevice={!filters.device_id} /> : <Spinner />}
        {more && (
          <div className="form-actions">
            <button type="button" className="btn btn-secondary" onClick={() => void loadMore()}>
              Load older
            </button>
          </div>
        )}
      </Card>
    </>
  );
}
