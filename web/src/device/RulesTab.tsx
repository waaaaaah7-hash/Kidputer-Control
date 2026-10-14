import { useEffect, useState, type FormEvent } from "react";
import { api, type AppCatalog, type Device, type SeenApp } from "../api";
import { DAYS } from "../format";
import { describeRule, type AppRule, type AppRuleIn, type RuleMode, type RuleWindow } from "../rules";
import { ActionButton, Card, Dialog, ErrorNote, Spinner, errorMessage, useLoader, useToast } from "../ui";

/** When apps may be used on this PC (roadmap #3), including the parents' own apps (#4). */
export default function RulesTab({ device }: { device: Device }) {
  const rules = useLoader(() => api.appRules(device.id), [device.id]);
  const catalog = useLoader(() => api.appCatalog(), []);
  const [editing, setEditing] = useState<AppRuleIn | null>(null);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [addingApp, setAddingApp] = useState(false);

  const save = async (next: AppRuleIn[]) => {
    await api.putAppRules(device.id, next);
    await rules.reload();
  };
  const current: AppRuleIn[] = (rules.data ?? []).map(({ target_kind, target, mode, windows }) => ({ target_kind, target, mode, windows }));

  return (
    <div className="columns">
      <div className="stack">
        <Card title="App rules" actions={<span className="muted small">{device.timezone}</span>}>
          <p className="muted small">
            Choose when apps or whole categories may be used. An app's own rule wins over its category's. Outside the allowed times the app
            closes within a second of opening, and your child sees when it's available. An app already open when its time ends gets a
            countdown first.
          </p>
          <ErrorNote error={rules.error ?? catalog.error} />
          {rules.data === undefined ? (
            <Spinner />
          ) : rules.data.length === 0 ? (
            <p className="muted">No rules yet: every app can be used whenever the PC is unlocked.</p>
          ) : (
            <ul className="list" data-testid="app-rules">
              {rules.data.map((r: AppRule, i) => (
                <li key={r.id} className="list-row">
                  <div className="grow">
                    <strong>{r.name}</strong> {r.target_kind === "category" && <span className="muted small">category</span>}
                    <div className="muted small">{describeRule(r)}</div>
                  </div>
                  <div className="row">
                    <button type="button" className="btn btn-ghost" onClick={() => { setEditing(current[i]!); setEditingIndex(i); }}>
                      Edit
                    </button>
                    <ActionButton variant="ghost" success="Rule removed" onClick={() => save(current.filter((_, j) => j !== i))}>
                      Remove
                    </ActionButton>
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-primary"
              disabled={!catalog.data}
              onClick={() => { setEditing({ target_kind: "category", target: "games", mode: "schedule", windows: defaultWindows() }); setEditingIndex(null); }}
            >
              Add a rule
            </button>
          </div>
        </Card>
      </div>
      <div className="stack">
        <CustomAppsCard catalog={catalog.data} onAdd={() => setAddingApp(true)} onChange={async () => { await catalog.reload(); await rules.reload(); }} />
        <CatalogCard catalog={catalog.data} />
      </div>
      {editing && catalog.data && (
        <RuleDialog
          catalog={catalog.data}
          initial={editing}
          taken={current.filter((_, j) => j !== editingIndex).map((r) => `${r.target_kind}:${r.target}`)}
          onCancel={() => setEditing(null)}
          onAddApp={() => setAddingApp(true)}
          onSave={async (rule) => {
            const next = editingIndex === null ? [...current, rule] : current.map((r, j) => (j === editingIndex ? rule : r));
            await save(next);
            setEditing(null);
          }}
        />
      )}
      <CustomAppDialog
        open={addingApp}
        device={device}
        catalog={catalog.data}
        onClose={() => setAddingApp(false)}
        onAdded={async (id) => {
          setAddingApp(false);
          await catalog.reload();
          if (editing) setEditing({ ...editing, target_kind: "app", target: id });
        }}
      />
    </div>
  );
}

function defaultWindows(): RuleWindow[] {
  return [0, 1, 2, 3, 4].map((day) => ({ day, start: "16:00", end: "19:00" })).concat([5, 6].map((day) => ({ day, start: "10:00", end: "20:00" })));
}

// --- Rule editor --------------------------------------------------------------------------------------------

function RuleDialog({
  catalog,
  initial,
  taken,
  onCancel,
  onSave,
  onAddApp,
}: {
  catalog: AppCatalog;
  initial: AppRuleIn;
  taken: string[];
  onCancel: () => void;
  onSave: (rule: AppRuleIn) => Promise<void>;
  onAddApp: () => void;
}) {
  const [rule, setRule] = useState<AppRuleIn>(initial);
  const [error, setError] = useState<string | null>(null);
  // An app added from inside this dialog becomes the rule's target; the rest of the edits are kept.
  useEffect(() => {
    setRule((r) => ({ ...r, target_kind: initial.target_kind, target: initial.target }));
  }, [initial.target_kind, initial.target]);
  const toast = useToast();
  const target = `${rule.target_kind}:${rule.target}`;
  const duplicate = taken.includes(target);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (rule.mode === "schedule" && rule.windows.length === 0) return setError("Choose at least one day.");
    if (rule.windows.some((w) => w.start === w.end)) return setError("Start and end times must differ.");
    try {
      await onSave(rule.mode === "schedule" ? rule : { ...rule, windows: [] });
      toast("Rule saved. The PC picks it up within a minute.");
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const categories = Object.entries(catalog.categories);
  return (
    <Dialog open onClose={onCancel} title={initial.target ? "App rule" : "Add a rule"}>
      <form className="stack" onSubmit={submit}>
        <label>
          Which apps
          <select
            aria-label="Which apps"
            value={target}
            onChange={(e) => {
              const [kind, ...rest] = e.target.value.split(":");
              setRule({ ...rule, target_kind: kind as "app" | "category", target: rest.join(":") });
            }}
          >
            <optgroup label="Whole categories">
              {categories.map(([id, label]) => (
                <option key={id} value={`category:${id}`}>
                  All {label.toLowerCase()}
                </option>
              ))}
            </optgroup>
            {categories.map(([cat, label]) => (
              <optgroup key={cat} label={label}>
                {catalog.apps
                  .filter((a) => a.category === cat)
                  .map((a) => (
                    <option key={a.id} value={`app:${a.id}`}>
                      {a.name}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </label>
        <button type="button" className="btn btn-ghost align-start" onClick={onAddApp}>
          An app that isn't listed…
        </button>
        {duplicate && <p className="warning-note">There's already a rule for this. Edit that one instead.</p>}

        <fieldset className="stack">
          <legend className="sr-only">When</legend>
          {(
            [
              ["schedule", "Only at these times"],
              ["allow", "Allowed any time"],
              ["block", "Blocked"],
            ] as [RuleMode, string][]
          ).map(([mode, label]) => (
            <label key={mode} className="inline">
              <input type="radio" name="rule-mode" checked={rule.mode === mode} onChange={() => setRule({ ...rule, mode, windows: rule.windows.length ? rule.windows : defaultWindows() })} />{" "}
              {label}
            </label>
          ))}
        </fieldset>
        {rule.mode === "schedule" && <WindowsEditor windows={rule.windows} onChange={(windows) => setRule({ ...rule, windows })} />}
        <ErrorNote error={error} />
        <div className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={onCancel}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={duplicate}>
            Save rule
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function WindowsEditor({ windows, onChange }: { windows: RuleWindow[]; onChange: (w: RuleWindow[]) => void }) {
  const byDay = (day: number) => windows.find((w) => w.day === day);
  const set = (day: number, patch: Partial<RuleWindow> | null) => {
    const others = windows.filter((w) => w.day !== day);
    if (patch === null) return onChange(others);
    const cur = byDay(day) ?? { day, start: "16:00", end: "19:00" };
    onChange([...others, { ...cur, ...patch }].sort((a, b) => a.day - b.day));
  };
  return (
    <table className="schedule">
      <tbody>
        {DAYS.map((name, day) => {
          const w = byDay(day);
          return (
            <tr key={name} className={w ? "" : "off"}>
              <th scope="row">
                <label className="inline">
                  <input type="checkbox" checked={!!w} onChange={(e) => set(day, e.target.checked ? {} : null)} /> {name}
                </label>
              </th>
              <td>
                <input type="time" aria-label={`${name} from`} disabled={!w} value={w?.start ?? "16:00"} onChange={(e) => set(day, { start: e.target.value })} />
              </td>
              <td>
                <input type="time" aria-label={`${name} until`} disabled={!w} value={w?.end ?? "19:00"} onChange={(e) => set(day, { end: e.target.value })} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// --- The parents' own apps (#4) ------------------------------------------------------------------------------

function CustomAppsCard({ catalog, onAdd, onChange }: { catalog?: AppCatalog; onAdd: () => void; onChange: () => Promise<void> }) {
  const custom = (catalog?.apps ?? []).filter((a) => a.custom);
  return (
    <Card title="Your own apps">
      <p className="muted small">Apps you added for rules, shared by all your PCs.</p>
      {custom.length === 0 ? (
        <p className="muted">None yet.</p>
      ) : (
        <ul className="list" data-testid="custom-apps">
          {custom.map((a) => (
            <li key={a.id} className="list-row">
              <div className="grow">
                <strong>{a.name}</strong> <span className="muted small">{catalog?.categories[a.category] ?? a.category}</span>
                <div className="muted small wrap-anywhere">{a.matchers.map((m) => Object.values(m).join(" · ")).join("; ")}</div>
              </div>
              <ActionButton
                variant="ghost"
                confirm={`Remove ${a.name}? Rules for it are removed on every PC.`}
                success="App removed"
                onClick={async () => {
                  await api.removeCustomApp(a.id);
                  await onChange();
                }}
              >
                Remove
              </ActionButton>
            </li>
          ))}
        </ul>
      )}
      <div className="form-actions">
        <button type="button" className="btn btn-secondary" onClick={onAdd}>
          Add an app
        </button>
      </div>
    </Card>
  );
}

function CustomAppDialog({
  open,
  device,
  catalog,
  onClose,
  onAdded,
}: {
  open: boolean;
  device: Device;
  catalog?: AppCatalog;
  onClose: () => void;
  onAdded: (id: string) => Promise<void>;
}) {
  const seen = useLoader(() => (open ? api.seenApps(device.id) : Promise.resolve([] as SeenApp[])), [device.id, open]);
  const [name, setName] = useState("");
  const [category, setCategory] = useState("games");
  const [newCategory, setNewCategory] = useState("");
  const [exe, setExe] = useState("");
  const [product, setProduct] = useState("");
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const pick = (a: SeenApp) => {
    setName(a.name);
    setExe(a.exe ?? "");
    setProduct(a.product ?? "");
  };

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const cat = category === "__new" ? newCategory.trim() : category;
    if (!cat) return setError("Name the new category.");
    try {
      const app = await api.addCustomApp({ name: name.trim(), category: cat, exe: exe.trim() || undefined, product: product.trim() || undefined });
      toast(`${app.name} added`);
      setName("");
      setExe("");
      setProduct("");
      await onAdded(app.id);
    } catch (err) {
      setError(errorMessage(err));
    }
  }

  const unlisted = (seen.data ?? []).filter((a) => !a.catalog_id);
  return (
    <Dialog open={open} onClose={onClose} title="Add an app">
      <form className="stack" onSubmit={submit}>
        {unlisted.length > 0 && (
          <>
            <p className="muted small">Apps your child used on this PC recently. Pick one, or fill in the details below.</p>
            <ul className="list seen-apps" data-testid="seen-apps">
              {unlisted.slice(0, 12).map((a) => (
                <li key={a.key} className="list-row">
                  <div className="grow">
                    <strong>{a.name}</strong>
                    <div className="muted small wrap-anywhere">{a.exe}</div>
                  </div>
                  <button type="button" className="btn btn-ghost" onClick={() => pick(a)}>
                    Use
                  </button>
                </li>
              ))}
            </ul>
          </>
        )}
        <label>
          Name
          <input required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Terraria" />
        </label>
        <label>
          Category
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            {Object.entries(catalog?.categories ?? {}).map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
            <option value="__new">A new category…</option>
          </select>
        </label>
        {category === "__new" && (
          <label>
            New category name
            <input maxLength={60} value={newCategory} onChange={(e) => setNewCategory(e.target.value)} placeholder="e.g. Homework apps" />
          </label>
        )}
        <label>
          Program file name
          <input maxLength={260} value={exe} onChange={(e) => setExe(e.target.value)} placeholder="e.g. Terraria.exe" />
        </label>
        <details>
          <summary className="muted small">More ways to recognise it</summary>
          <label>
            Text in its product name
            <input maxLength={200} value={product} onChange={(e) => setProduct(e.target.value)} placeholder="e.g. Terraria" />
          </label>
        </details>
        <p className="muted small">The file name still matches if your child renames the program, as long as it's the same program.</p>
        <ErrorNote error={error} />
        <div className="form-actions">
          <button type="button" className="btn btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!name.trim() || (!exe.trim() && !product.trim())}>
            Add app
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function CatalogCard({ catalog }: { catalog?: AppCatalog }) {
  if (!catalog) return null;
  return (
    <Card title="Recognised apps">
      <p className="muted small">These are recognised even if your child renames them.</p>
      {Object.entries(catalog.categories).map(([cat, label]) => {
        const apps = catalog.apps.filter((a) => a.category === cat);
        return apps.length ? (
          <p key={cat} className="small">
            <strong>{label}:</strong> {apps.map((a) => a.name).join(", ")}
          </p>
        ) : null;
      })}
      <p className="muted small">Web versions of apps (in a browser) can't be controlled this way.</p>
    </Card>
  );
}
