import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { FileUp, PencilLine, Plus, RefreshCw, ShieldCheck, UserPlus, X } from "lucide-react";
import ComplaintMap from "../components/ComplaintMap";
import { useConfirm, useToast } from "../components/feedback";
import { EmptyState, ErrorState, LoadingState, Panel, formatDateTime } from "../components/ui";
import { api, slugify } from "../lib/api";
import { formatHours, formatPercent, groupStats } from "../lib/analytics";

function Modal({ title, subtitle, onClose, onSubmit, busy, submitLabel = "Save", children, size = "" }) {
  const ref = useAutoFocus();
  return (
    <div className="ct-modal-backdrop" role="presentation" onKeyDown={(e) => e.key === "Escape" && onClose()}>
      <form ref={ref} className={`ct-modal ${size}`} role="dialog" aria-modal="true" aria-label={title} onSubmit={(e) => { e.preventDefault(); onSubmit(); }}>
        <div className="ct-modal-head">
          <div><h3>{title}</h3>{subtitle && <p className="ct-muted">{subtitle}</p>}</div>
          <button type="button" className="ct-icon-btn" onClick={onClose} aria-label="Close"><X size={19} /></button>
        </div>
        {children}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={busy}>{busy ? "Saving..." : submitLabel}</button>
        </div>
      </form>
    </div>
  );
}

/** Move keyboard focus into a dialog's first field when it opens. */
function useAutoFocus() {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.querySelector("input:not([type=hidden]):not([disabled]), select, textarea, button[type=submit]")?.focus();
  }, []);
  return ref;
}

function useSaver(reload) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (fn, message) => {
    setBusy(true);
    try {
      const result = await fn();
      toast(message);
      await reload?.();
      return result ?? true;
    } catch (err) {
      toast(err.message, "error");
      return false;
    } finally {
      setBusy(false);
    }
  }, [reload, toast]);
  return [busy, run];
}

// ---------------- Departments & categories ----------------

export function DepartmentsView({ auth, reference, reloadReference, complaints }) {
  const [editing, setEditing] = useState(null);
  const [categoryEdit, setCategoryEdit] = useState(null);
  const [busy, run] = useSaver(reloadReference);
  const stats = useMemo(() => Object.fromEntries(groupStats(complaints, (c) => c.department).map((r) => [r.key, r])), [complaints]);
  const builtin = reference.source !== "stored";

  async function saveDept() {
    const id = editing.id || slugify(editing.name);
    if (await run(() => api.saveDepartment(auth, id, { name: editing.name.trim(), description: editing.description || "", active: editing.active !== false }), "Department saved.")) setEditing(null);
  }

  async function saveCategory() {
    if (await run(() => api.saveCategory(auth, categoryEdit.name.trim(), { departmentId: categoryEdit.departmentId, active: categoryEdit.active !== false }), "Category routing saved.")) setCategoryEdit(null);
  }

  return (
    <>
      {builtin && (
        <div className="ct-banner ct-banner-info">
          <ShieldCheck size={16} /> Using CivicTrack's built-in departments. Saving any change stores them in the configuration table so you can manage them.
          <button type="button" className="ct-btn ct-btn-sm" disabled={busy} onClick={() => run(() => api.initializeReference(auth), "Built-in departments and categories saved.")}>Save defaults now</button>
        </div>
      )}
      <Panel title="Departments" subtitle="Workload and performance are computed from current complaint records"
        actions={<button type="button" className="ct-btn ct-btn-primary" onClick={() => setEditing({ name: "", description: "", active: true })}><Plus size={15} /> Add department</button>}>
        <div className="ct-table-wrap">
          <table className="ct-table">
            <thead><tr><th>Department</th><th>Status</th><th>Total</th><th>Open</th><th>Overdue</th><th>Avg. resolution</th><th>SLA met</th><th><span className="ct-sr">Edit</span></th></tr></thead>
            <tbody>
              {reference.departments.map((d) => {
                const s = stats[d.name] || {};
                return (
                  <tr key={d.id}>
                    <td className="ct-cell-main"><strong>{d.name}</strong><span className="ct-muted ct-small">{d.description || d.id}</span></td>
                    <td>{d.active === false ? <span className="ct-badge ct-status-closed">Inactive</span> : <span className="ct-badge ct-status-resolved">Active</span>}</td>
                    <td>{s.total || 0}</td><td>{s.open || 0}</td><td className={s.overdue ? "ct-text-danger" : ""}>{s.overdue || 0}</td>
                    <td>{formatHours(s.avgResolutionHours ?? null)}</td><td>{formatPercent(s.slaRate ?? null)}</td>
                    <td><button type="button" className="ct-icon-btn" onClick={() => setEditing({ ...d })} aria-label={`Edit ${d.name}`}><PencilLine size={16} /></button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Category routing" subtitle="New complaints are routed to the department mapped to their category"
        actions={<button type="button" className="ct-btn ct-btn-ghost" onClick={() => setCategoryEdit({ name: "", departmentId: reference.departments[0]?.id, active: true, isNew: true })}><Plus size={15} /> Add category</button>}>
        <div className="ct-table-wrap">
          <table className="ct-table">
            <thead><tr><th>Category</th><th>Routed to</th><th>Shown to citizens</th><th><span className="ct-sr">Edit</span></th></tr></thead>
            <tbody>
              {reference.categories.map((c) => (
                <tr key={c.name}>
                  <td><strong>{c.name}</strong></td>
                  <td>{reference.departments.find((d) => d.id === c.departmentId)?.name || <span className="ct-text-danger">Unknown department</span>}</td>
                  <td>{c.active === false ? "No" : "Yes"}</td>
                  <td><button type="button" className="ct-icon-btn" onClick={() => setCategoryEdit({ ...c })} aria-label={`Edit ${c.name}`}><PencilLine size={16} /></button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      {editing && (
        <Modal title={editing.id ? "Edit department" : "Add department"} onClose={() => setEditing(null)} onSubmit={saveDept} busy={busy}>
          <label className="ct-field">Name<input required maxLength={80} value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
          {!editing.id && editing.name && <small className="ct-muted">ID: {slugify(editing.name)}</small>}
          <label className="ct-field">Description<input maxLength={300} value={editing.description || ""} onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></label>
          <label className="ct-check"><input type="checkbox" checked={editing.active !== false} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active</label>
        </Modal>
      )}
      {categoryEdit && (
        <Modal title={categoryEdit.isNew ? "Add category" : `Route “${categoryEdit.name}”`} onClose={() => setCategoryEdit(null)} onSubmit={saveCategory} busy={busy}>
          {categoryEdit.isNew && <label className="ct-field">Category name<input required maxLength={60} value={categoryEdit.name} onChange={(e) => setCategoryEdit({ ...categoryEdit, name: e.target.value })} /></label>}
          <label className="ct-field">Department<select value={categoryEdit.departmentId} onChange={(e) => setCategoryEdit({ ...categoryEdit, departmentId: e.target.value })}>
            {reference.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
          </select></label>
          <label className="ct-check"><input type="checkbox" checked={categoryEdit.active !== false} onChange={(e) => setCategoryEdit({ ...categoryEdit, active: e.target.checked })} /> Citizens can choose this category</label>
        </Modal>
      )}
    </>
  );
}

// ---------------- Zones, wards & GeoJSON ----------------

function GeoJsonImport({ auth, zones, onDone }) {
  const toast = useToast();
  const [level, setLevel] = useState("ward");
  const [collection, setCollection] = useState(null);
  const [fileName, setFileName] = useState("");
  const [props, setProps] = useState({ nameProperty: "", idProperty: "", numberProperty: "", zoneProperty: "", source: "" });
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const keys = collection ? Object.keys(collection.features?.[0]?.properties || {}) : [];

  async function readFile(file) {
    setResult(null);
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { toast("GeoJSON must be 5 MB or smaller; simplify it first (e.g. mapshaper.org).", "error"); return; }
    try {
      const parsed = JSON.parse(await file.text());
      if (parsed.type !== "FeatureCollection" || !Array.isArray(parsed.features)) throw new Error("File is not a GeoJSON FeatureCollection.");
      const first = Object.keys(parsed.features[0]?.properties || {});
      setCollection(parsed);
      setFileName(file.name);
      setProps((p) => ({ ...p, nameProperty: first.find((k) => /name/i.test(k)) || first[0] || "", numberProperty: first.find((k) => /(no|num|number|code)$/i.test(k)) || "" }));
    } catch (err) {
      toast(err.message || "Could not read the GeoJSON file.", "error");
    }
  }

  async function submit() {
    setBusy(true);
    try {
      const body = { level, featureCollection: collection, ...Object.fromEntries(Object.entries(props).filter(([, v]) => v)) };
      const data = await api.importBoundaries(auth, body);
      setResult(data);
      toast(`Imported ${data.imported.length} ${level}(s); skipped ${data.skipped.length}.`);
      onDone();
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setBusy(false);
    }
  }

  const select = (key, label, optional = true) => (
    <label className="ct-field">{label}
      <select value={props[key]} onChange={(e) => setProps({ ...props, [key]: e.target.value })}>
        {optional && <option value="">— none —</option>}
        {keys.map((k) => <option key={k}>{k}</option>)}
      </select>
    </label>
  );

  return (
    <Panel title="Import boundaries (GeoJSON)" subtitle="Upload an official zone or ward boundary file (FeatureCollection of Polygon/MultiPolygon, WGS84 longitude/latitude). CivicTrack does not ship boundary data.">
      <div className="ct-form-grid">
        <label className="ct-field">Boundary level<select value={level} onChange={(e) => setLevel(e.target.value)}><option value="ward">Wards</option><option value="zone">Zones</option></select></label>
        <label className="ct-field">GeoJSON file<input type="file" accept=".geojson,.json,application/geo+json,application/json" onChange={(e) => readFile(e.target.files?.[0])} /></label>
        {collection && (
          <>
            <p className="ct-span-2 ct-small">{fileName}: {collection.features.length} feature(s). Choose which properties hold the names.</p>
            {select("nameProperty", "Name property", false)}
            {select("idProperty", "ID property (optional)")}
            {level === "ward" && select("numberProperty", "Ward number property (optional)")}
            {level === "ward" && select("zoneProperty", `Zone property (optional; must match one of ${zones.length} zone(s))`)}
            <label className="ct-field ct-span-2">Data source / licence<input maxLength={200} value={props.source} onChange={(e) => setProps({ ...props, source: e.target.value })} placeholder="e.g. BBMP ward boundaries 2023, source URL, licence" /></label>
          </>
        )}
      </div>
      <div className="ct-modal-actions">
        <button type="button" className="ct-btn ct-btn-primary" disabled={!collection || !props.nameProperty || busy} onClick={submit}><FileUp size={15} /> {busy ? "Importing..." : "Import"}</button>
      </div>
      {result?.skipped?.length > 0 && (
        <details className="ct-mt"><summary>{result.skipped.length} feature(s) skipped</summary>
          <ul className="ct-small">{result.skipped.map((s) => <li key={s.index}>#{s.index} {s.name || ""}: {s.reason}</li>)}</ul>
        </details>
      )}
    </Panel>
  );
}

export function AreasView({ auth, reference, reloadReference, complaints }) {
  const [editing, setEditing] = useState(null); // {level, id, name, number, zoneId, active}
  const [busy, run] = useSaver(reloadReference);
  const zoneStats = useMemo(() => Object.fromEntries(groupStats(complaints, (c) => c.zoneId || c.zone).map((r) => [r.key, r])), [complaints]);
  const wardStats = useMemo(() => Object.fromEntries(groupStats(complaints, (c) => c.wardId || c.ward).map((r) => [r.key, r])), [complaints]);
  const legacyAreas = groupStats(complaints.filter((c) => (c.ward || c.zone) && !c.wardId && !c.zoneId), (c) => `${c.zone || "—"} / ${c.ward || "—"}`);

  async function save() {
    const { level, ...item } = editing;
    const id = item.id || slugify(item.name);
    const body = level === "zone" ? { name: item.name.trim(), active: item.active !== false } : { name: item.name.trim(), number: item.number || "", zoneId: item.zoneId || "", active: item.active !== false };
    if (await run(() => (level === "zone" ? api.saveZone(auth, id, body) : api.saveWard(auth, id, body)), `${level === "zone" ? "Zone" : "Ward"} saved.`)) setEditing(null);
  }

  const row = (area, level, stats) => {
    const s = stats[area.id] || {};
    return (
      <tr key={area.id}>
        <td className="ct-cell-main"><strong>{area.name}{area.number ? ` (${area.number})` : ""}</strong><span className="ct-muted ct-small">{area.id}</span></td>
        {level === "ward" && <td>{reference.zones.find((z) => z.id === area.zoneId)?.name || "—"}</td>}
        <td>{area.boundary ? `Yes (${area.boundaryVertices || "?"} pts)` : "No"}</td>
        <td>{s.total || 0}</td><td>{s.open || 0}</td><td className={s.overdue ? "ct-text-danger" : ""}>{s.overdue || 0}</td><td>{formatPercent(s.slaRate ?? null)}</td>
        <td><button type="button" className="ct-icon-btn" aria-label={`Edit ${area.name}`} onClick={() => setEditing({ level, ...area })}><PencilLine size={16} /></button></td>
      </tr>
    );
  };

  return (
    <>
      <Panel title="Zones" subtitle="City → Zone → Ward hierarchy" actions={<button type="button" className="ct-btn ct-btn-primary" onClick={() => setEditing({ level: "zone", name: "", active: true })}><Plus size={15} /> Add zone</button>}>
        {reference.zones.length ? (
          <div className="ct-table-wrap"><table className="ct-table">
            <thead><tr><th>Zone</th><th>Boundary</th><th>Total</th><th>Open</th><th>Overdue</th><th>SLA met</th><th><span className="ct-sr">Edit</span></th></tr></thead>
            <tbody>{reference.zones.map((z) => row(z, "zone", zoneStats))}</tbody>
          </table></div>
        ) : <EmptyState title="No zones defined" message="Add zones manually or import an official GeoJSON boundary file below." />}
      </Panel>
      <Panel title="Wards" actions={<button type="button" className="ct-btn ct-btn-primary" onClick={() => setEditing({ level: "ward", name: "", number: "", zoneId: reference.zones[0]?.id || "", active: true })}><Plus size={15} /> Add ward</button>}>
        {reference.wards.length ? (
          <div className="ct-table-wrap"><table className="ct-table">
            <thead><tr><th>Ward</th><th>Zone</th><th>Boundary</th><th>Total</th><th>Open</th><th>Overdue</th><th>SLA met</th><th><span className="ct-sr">Edit</span></th></tr></thead>
            <tbody>{reference.wards.map((w) => row(w, "ward", wardStats))}</tbody>
          </table></div>
        ) : <EmptyState title="No wards defined" message="Wards with boundaries let CivicTrack locate new complaints automatically." />}
      </Panel>
      {reference.wards.some((w) => w.boundary) && (
        <Panel title="Ward complaint density" subtitle="Shading = number of complaints located in each ward">
          <ComplaintMap complaints={complaints} boundaries={reference.wards} areaKey="wardId" height={420} showModeSwitch />
        </Panel>
      )}
      <GeoJsonImport auth={auth} zones={reference.zones} onDone={reloadReference} />
      {legacyAreas.length > 0 && (
        <Panel title="Free-text areas on older complaints" subtitle="Assigned before managed zones/wards existed. Re-classify them from the complaint drawer.">
          <ul className="ct-small">{legacyAreas.map((a) => <li key={a.key}>{a.key}: {a.total} complaint(s)</li>)}</ul>
        </Panel>
      )}
      {editing && (
        <Modal title={`${editing.id ? "Edit" : "Add"} ${editing.level}`} onClose={() => setEditing(null)} onSubmit={save} busy={busy}>
          <label className="ct-field">Name<input required maxLength={80} value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></label>
          {editing.level === "ward" && (
            <>
              <label className="ct-field">Ward number<input maxLength={10} value={editing.number || ""} onChange={(e) => setEditing({ ...editing, number: e.target.value })} /></label>
              <label className="ct-field">Zone<select value={editing.zoneId || ""} onChange={(e) => setEditing({ ...editing, zoneId: e.target.value })}>
                <option value="">— none —</option>{reference.zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
              </select></label>
            </>
          )}
          <label className="ct-check"><input type="checkbox" checked={editing.active !== false} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active</label>
          <small className="ct-muted">Boundaries are added through GeoJSON import; editing here keeps any existing boundary.</small>
        </Modal>
      )}
    </>
  );
}

// ---------------- Officers & users ----------------

export function OfficersView({ auth, reference, officers, source, officerError, reloadOfficers, showComplaints }) {
  const [profile, setProfile] = useState(null);
  const [busy, run] = useSaver(reloadOfficers);

  async function save() {
    const { sub, departmentId, zoneIds, wardIds, available, maxActive } = profile;
    if (await run(() => api.saveOfficerProfile(auth, sub, { departmentId, zoneIds, wardIds, available, maxActive: Number(maxActive) }), "Officer profile saved.")) setProfile(null);
  }

  const toggle = (field, id) => setProfile((p) => ({ ...p, [field]: p[field].includes(id) ? p[field].filter((x) => x !== id) : [...p[field], id] }));

  return (
    <Panel title="Officer directory & workload" subtitle={source === "cognito" ? "Members of the Cognito Officer group" : source === "local-demo" ? "Local demo officers" : "Officers referenced by assignments"}>
      {source === "fallback" && <div className="ct-banner ct-banner-info">Officer directory unavailable ({officerError}). Showing officers from existing assignments.</div>}
      {officers.length ? (
        <div className="ct-table-wrap">
          <table className="ct-table">
            <thead><tr><th>Officer</th><th>Department</th><th>Areas</th><th>Availability</th><th>Active</th><th>Overdue</th><th>Completed</th><th>Avg. resolution</th><th>SLA met</th><th><span className="ct-sr">Edit</span></th></tr></thead>
            <tbody>
              {officers.map((o) => {
                const p = o.profile || {};
                const areas = [...(p.zoneIds || []).map((id) => reference.zones.find((z) => z.id === id)?.name), ...(p.wardIds || []).map((id) => reference.wards.find((w) => w.id === id)?.name)].filter(Boolean);
                return (
                  <tr key={o.sub}>
                    <td className="ct-cell-main"><strong>{o.displayName}</strong><span className="ct-muted ct-small">{o.email}</span></td>
                    <td>{reference.departments.find((d) => d.id === p.departmentId)?.name || "—"}</td>
                    <td className="ct-small">{areas.length ? areas.join(", ") : "—"}</td>
                    <td>{o.enabled === false ? <span className="ct-badge ct-status-closed">Disabled</span> : p.available === false ? <span className="ct-badge ct-status-pending">Unavailable</span> : <span className="ct-badge ct-status-resolved">Available</span>}</td>
                    <td><button type="button" className="ct-link" onClick={() => showComplaints({ search: o.displayName })}>{o.active}</button>{p.maxActive ? <span className="ct-muted"> / {p.maxActive}</span> : null}</td>
                    <td className={o.overdue ? "ct-text-danger" : ""}>{o.overdue}</td>
                    <td>{o.completed}</td><td>{formatHours(o.avgResolutionHours)}</td><td>{formatPercent(o.slaRate)}</td>
                    <td>{source !== "fallback" && <button type="button" className="ct-icon-btn" aria-label={`Edit profile of ${o.displayName}`} onClick={() => setProfile({ sub: o.sub, name: o.displayName, departmentId: p.departmentId || "", zoneIds: p.zoneIds || [], wardIds: p.wardIds || [], available: p.available !== false, maxActive: p.maxActive || 15 })}><PencilLine size={16} /></button>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : <EmptyState title="No officers found" message="Invite officers from Users & Roles." />}
      {profile && (
        <Modal title={`Officer profile: ${profile.name}`} subtitle="Used for assignment suggestions and workload limits" onClose={() => setProfile(null)} onSubmit={save} busy={busy} size="ct-modal-lg">
          <div className="ct-form-grid">
            <label className="ct-field">Department<select value={profile.departmentId} onChange={(e) => setProfile({ ...profile, departmentId: e.target.value })}>
              <option value="">— none —</option>{reference.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
            </select></label>
            <label className="ct-field">Maximum active complaints<input type="number" min={1} max={200} value={profile.maxActive} onChange={(e) => setProfile({ ...profile, maxActive: e.target.value })} /></label>
            <label className="ct-check ct-span-2"><input type="checkbox" checked={profile.available} onChange={(e) => setProfile({ ...profile, available: e.target.checked })} /> Available for new assignments</label>
            <fieldset className="ct-span-2 ct-checklist"><legend>Zones</legend>
              {reference.zones.length ? reference.zones.map((z) => <label key={z.id} className="ct-check"><input type="checkbox" checked={profile.zoneIds.includes(z.id)} onChange={() => toggle("zoneIds", z.id)} /> {z.name}</label>) : <span className="ct-muted ct-small">No zones defined yet.</span>}
            </fieldset>
            <fieldset className="ct-span-2 ct-checklist"><legend>Wards</legend>
              {reference.wards.length ? reference.wards.map((w) => <label key={w.id} className="ct-check"><input type="checkbox" checked={profile.wardIds.includes(w.id)} onChange={() => toggle("wardIds", w.id)} /> {w.name}</label>) : <span className="ct-muted ct-small">No wards defined yet.</span>}
            </fieldset>
          </div>
        </Modal>
      )}
    </Panel>
  );
}

export function UsersView({ auth, onChanged }) {
  const toast = useToast();
  const confirm = useConfirm();
  const [role, setRole] = useState("Officer");
  const [state, setState] = useState({ users: [], loading: true, error: "", nextToken: null });
  const [invite, setInvite] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (token) => {
    setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      const data = await api.listUsers(auth, role, token);
      setState((s) => ({ users: token ? [...s.users, ...data.users] : data.users, loading: false, error: "", nextToken: data.nextToken }));
    } catch (err) {
      setState({ users: [], loading: false, error: err.message, nextToken: null });
    }
  }, [auth, role]);
  useEffect(() => {
    const run = async () => { await load(); };
    run();
  }, [load]);

  async function sendInvite() {
    setBusy(true);
    try {
      const data = await api.createUser(auth, invite);
      toast(`${data.user.email} invited as ${data.user.role}. Cognito emailed a temporary password.`);
      setInvite(null);
      onChanged?.();
      load();
    } catch (err) { toast(err.message, "error"); } finally { setBusy(false); }
  }

  async function change(user, body, message) {
    const ok = await confirm({ title: message, message: `${user.email}. Role changes apply at the user's next sign-in.`, confirmLabel: "Confirm", tone: body.enabled === false ? "danger" : "primary" });
    if (!ok) return;
    try {
      await api.updateUser(auth, user.username, body);
      toast("User updated.");
      onChanged?.();
      load();
    } catch (err) { toast(err.message, "error"); }
  }

  return (
    <Panel title="Users & roles" subtitle="Accounts live in Amazon Cognito. Citizens self-register; officers and administrators are invited here."
      actions={<button type="button" className="ct-btn ct-btn-primary" onClick={() => setInvite({ email: "", name: "", role: "Officer" })}><UserPlus size={15} /> Invite staff</button>}>
      <div className="ct-filters" role="group" aria-label="Role">
        {["Officer", "Admin", "Citizen"].map((r) => <button key={r} type="button" className={`ct-chip ${role === r ? "active" : ""}`} aria-pressed={role === r} onClick={() => setRole(r)}>{r}s</button>)}
        <button type="button" className="ct-icon-btn" onClick={() => load()} aria-label="Reload users"><RefreshCw size={16} /></button>
      </div>
      {state.loading && !state.users.length ? <LoadingState label="Loading users..." /> : state.error ? <ErrorState message={state.error} onRetry={() => load()} /> : state.users.length ? (
        <div className="ct-table-wrap">
          <table className="ct-table">
            <thead><tr><th>User</th><th>Status</th><th>Sign-in</th><th>Change role</th><th><span className="ct-sr">Access</span></th></tr></thead>
            <tbody>
              {state.users.map((u) => (
                <tr key={u.username}>
                  <td className="ct-cell-main"><strong>{u.name || u.email}</strong><span className="ct-muted ct-small">{u.email}</span></td>
                  <td className="ct-small">{u.status}</td>
                  <td>{u.enabled ? <span className="ct-badge ct-status-resolved">Enabled</span> : <span className="ct-badge ct-status-closed">Disabled</span>}</td>
                  <td>
                    <select aria-label={`Role of ${u.email}`} value={role} onChange={(e) => change(u, { role: e.target.value }, `Change role to ${e.target.value}?`)}>
                      {["Citizen", "Officer", "Admin"].map((r) => <option key={r}>{r}</option>)}
                    </select>
                  </td>
                  <td>
                    <button type="button" className="ct-btn ct-btn-sm" onClick={() => change(u, { enabled: !u.enabled }, u.enabled ? "Disable sign-in?" : "Enable sign-in?")}>{u.enabled ? "Disable" : "Enable"}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <EmptyState title={`No ${role.toLowerCase()}s`} />}
      {state.nextToken && <button type="button" className="ct-btn ct-btn-ghost ct-mt" onClick={() => load(state.nextToken)}>Load more</button>}
      <p className="ct-muted ct-small ct-mt">You cannot change your own role or access. Every change is recorded in the audit log.</p>
      {invite && (
        <Modal title="Invite staff member" onClose={() => setInvite(null)} onSubmit={sendInvite} busy={busy} submitLabel="Send invitation">
          <label className="ct-field">Full name<input required maxLength={100} value={invite.name} onChange={(e) => setInvite({ ...invite, name: e.target.value })} /></label>
          <label className="ct-field">Email<input required type="email" maxLength={254} value={invite.email} onChange={(e) => setInvite({ ...invite, email: e.target.value })} /></label>
          <label className="ct-field">Role<select value={invite.role} onChange={(e) => setInvite({ ...invite, role: e.target.value })}><option>Officer</option><option>Admin</option></select></label>
          <small className="ct-muted">Cognito sends the invitation email (subject to SES/Cognito email limits).</small>
        </Modal>
      )}
    </Panel>
  );
}

// ---------------- Settings & audit ----------------

export function SettingsView({ auth, reference, reloadReference }) {
  const [form, setForm] = useState(reference.settings);
  const [busy, run] = useSaver(reloadReference);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : Number(e.target.value) });
  return (
    <Panel title="Configuration" subtitle="Applies to new complaints and the next SLA sweep">
      <form className="ct-form-grid" onSubmit={(e) => { e.preventDefault(); run(() => api.saveSettings(auth, form), "Settings saved."); }}>
        <label className="ct-field">SLA resolution target (hours)<input type="number" min={1} max={720} value={form.slaHours} onChange={set("slaHours")} /></label>
        <label className="ct-field">Warning before deadline (hours)<input type="number" min={1} max={168} value={form.warningHours} onChange={set("warningHours")} /></label>
        <label className="ct-field">Max complaints per citizen per hour<input type="number" min={1} max={100} value={form.maxComplaintsPerHour} onChange={set("maxComplaintsPerHour")} /></label>
        <label className="ct-check ct-span-2"><input type="checkbox" checked={form.discloseOfficerName} onChange={set("discloseOfficerName")} /> Show the assigned officer's name to citizens</label>
        <div className="ct-span-2"><button type="submit" className="ct-btn ct-btn-primary" disabled={busy}>Save settings</button></div>
      </form>
      <p className="ct-muted ct-small ct-mt">Existing complaints keep the SLA hours they were created with. Second-level escalation happens 24 h after a missed deadline.</p>
    </Panel>
  );
}

export function AuditView({ auth }) {
  const [month, setMonth] = useState(() => new Date().toISOString().slice(0, 7));
  const [state, setState] = useState({ entries: [], loading: true, error: "" });
  useEffect(() => {
    let cancelled = false;
    api.auditLog(auth, month)
      .then((d) => { if (!cancelled) setState({ entries: d.entries, loading: false, error: "" }); })
      .catch((err) => { if (!cancelled) setState({ entries: [], loading: false, error: err.message }); });
    return () => { cancelled = true; };
  }, [auth, month]);
  return (
    <Panel title="Audit log" subtitle="Administrative actions: users, roles, configuration, assignments, rejections and escalations"
      actions={<label className="ct-select"><span>Month</span><input type="month" value={month} onChange={(e) => { setState((s) => ({ ...s, loading: true })); setMonth(e.target.value); }} /></label>}>
      {state.loading ? <LoadingState /> : state.error ? <ErrorState message={state.error} /> : state.entries.length ? (
        <div className="ct-table-wrap">
          <table className="ct-table">
            <thead><tr><th>Time</th><th>Action</th><th>Target</th><th>Actor</th><th>Details</th></tr></thead>
            <tbody>
              {state.entries.map((e) => (
                <tr key={`${e.at}-${e.target}-${e.action}`}>
                  <td className="ct-small">{formatDateTime(e.at)}</td><td>{e.action.replace(/_/g, " ")}</td><td className="ct-small">{e.target}</td>
                  <td className="ct-small">{e.actorRole} · {String(e.actorSub).slice(0, 8)}…</td>
                  <td className="ct-small"><code>{e.details ? JSON.stringify(e.details).slice(0, 160) : ""}</code></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : <EmptyState title="No audit entries for this month" />}
    </Panel>
  );
}
