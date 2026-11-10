import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, BarChart3, Building2, CheckCircle2, ClipboardList, Clock3, FileClock, LayoutDashboard, Map as MapIcon, MapPinned,
  PencilLine, Settings, ShieldAlert, TrendingUp, UserCog, UserMinus, UserPlus, Users, X, XCircle,
} from "lucide-react";
import PortalShell from "../components/PortalShell";
import ComplaintDetail from "../components/ComplaintDetail";
import ComplaintMap from "../components/ComplaintMap";
import { BarList } from "../components/Charts";
import { useConfirm, useToast } from "../components/feedback";
import { ComplaintTable, EMPTY_FILTERS, FilterBar, useAuthContext, useComplaints } from "../components/complaints";
import { useNotifications, useReference } from "../components/data";
import { EmptyState, ErrorState, LoadingState, Panel, StatCard, useNow } from "../components/ui";
import { api } from "../lib/api";
import { applyFilters, average, downloadFile, groupStats, resolutionHours, slaCompliance, summarize, toCsv } from "../lib/analytics";
import { nearby } from "../lib/geo";
import { rankOfficers } from "../lib/assignment";
import { OPEN_STATUSES, PRIORITIES, canTransition, slaState } from "../lib/lifecycle";
import AnalyticsView from "./AnalyticsView";
import { AreasView, AuditView, DepartmentsView, OfficersView, SettingsView, UsersView } from "./ManagementViews";

const NAV = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "complaints", label: "Complaints", icon: ClipboardList },
  { id: "map", label: "City Map", icon: MapIcon },
  { id: "analytics", label: "Analytics & Reports", icon: BarChart3 },
  { id: "officers", label: "Officers", icon: Users },
  { id: "users", label: "Users & Roles", icon: UserCog },
  { id: "departments", label: "Departments", icon: Building2 },
  { id: "areas", label: "Zones & Wards", icon: MapPinned },
  { id: "settings", label: "Settings", icon: Settings },
  { id: "audit", label: "Audit Log", icon: FileClock },
];

function stamp() {
  return new Date().toISOString().slice(0, 10);
}

/** Officer directory (Cognito + profiles); falls back to officers seen in assignments. */
function useOfficers(auth, complaints) {
  const [state, setState] = useState({ officers: [], source: "loading", error: "" });
  const [version, setVersion] = useState(0);
  useEffect(() => {
    let cancelled = false;
    api.listOfficers(auth)
      .then((data) => { if (!cancelled) setState({ officers: data.officers || [], source: data.source || "cognito", error: "" }); })
      .catch((err) => { if (!cancelled) setState({ officers: [], source: "fallback", error: err.message }); });
    return () => { cancelled = true; };
  }, [auth, version]);

  const officers = useMemo(() => {
    const base = state.source === "fallback"
      ? [...new Map(complaints.filter((c) => c.assignedOfficerSub).map((c) => [c.assignedOfficerSub, { sub: c.assignedOfficerSub, name: c.assignedOfficerName, email: "", enabled: true }])).values()]
      : state.officers;
    return base.map((o) => {
      const mine = complaints.filter((c) => c.assignedOfficerSub === o.sub);
      const active = mine.filter((c) => OPEN_STATUSES.has(c.status));
      const done = complaints.filter((c) => !OPEN_STATUSES.has(c.status) && c.status !== "REJECTED" && c.resolvedBySub === o.sub);
      return {
        ...o,
        displayName: o.name || o.email || o.sub,
        active: active.length,
        overdue: active.filter((c) => c.sla.state === "breached").length,
        completed: done.length,
        avgResolutionHours: average(done.map(resolutionHours)),
        slaRate: slaCompliance(done).rate,
        activeComplaints: active,
      };
    }).sort((a, b) => a.displayName.localeCompare(b.displayName));
  }, [state, complaints]);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  return { officers, source: state.source, error: state.error, reload };
}

function AssignModal({ complaint, officers, departments, source, onSubmit, onCancel }) {
  const [officerSub, setOfficerSub] = useState("");
  const [manualSub, setManualSub] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reassign = complaint.status === "ASSIGNED";
  const ranked = rankOfficers(complaint, officers.filter((o) => !(reassign && o.sub === complaint.assignedOfficerSub)), departments);
  const chosen = ranked.find((o) => o.sub === officerSub);

  async function submit(e) {
    e.preventDefault();
    const sub = officerSub || manualSub.trim();
    if (!sub) { setError("Choose an officer."); return; }
    setBusy(true);
    setError("");
    try { await onSubmit(sub, reason.trim(), chosen?.unavailable === true); } catch (err) { setError(err.message); setBusy(false); }
  }

  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal ct-modal-lg" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="assign-title">
        <div className="ct-modal-head">
          <div><h3 id="assign-title">{reassign ? "Reassign complaint" : "Assign complaint"}</h3><p className="ct-muted">{complaint.id} · {complaint.title} · {complaint.department}{complaint.ward ? ` · ${complaint.ward}` : ""}</p></div>
          <button type="button" className="ct-icon-btn" onClick={onCancel} aria-label="Close"><X size={19} /></button>
        </div>
        {reassign && <p className="ct-small">Currently assigned to <strong>{complaint.assignedOfficerName}</strong>. Reassignment is only possible before work starts; the previous officer is notified.</p>}
        {ranked.length > 0 ? (
          <fieldset className="ct-officer-pick">
            <legend>Select officer — ranked by department, area, nearby work, capacity and availability</legend>
            {ranked.map((o, i) => (
              <label key={o.sub} className={`ct-officer-option ${officerSub === o.sub ? "active" : ""} ${o.unavailable ? "ct-unavailable" : ""}`}>
                <input type="radio" name="officer" autoFocus={i === 0} value={o.sub} checked={officerSub === o.sub} onChange={() => setOfficerSub(o.sub)} />
                <span><strong>{o.displayName}</strong>{i === 0 && !o.unavailable && <span className="ct-badge ct-status-resolved ct-ml">Suggested</span>}{o.email && <small className="ct-muted"> · {o.email}</small>}</span>
                <span className="ct-small ct-muted">{o.active}/{o.profile?.maxActive || 15} active{o.reasons.length ? ` · ${o.reasons.join(" · ")}` : ""}</span>
              </label>
            ))}
          </fieldset>
        ) : <p className="ct-muted">No officers available in the directory.</p>}
        {chosen?.unavailable && <p className="ct-error">This officer is marked unavailable. Assigning will override that setting.</p>}
        {source === "fallback" && (
          <label className="ct-field">Officer Cognito user ID (sub)
            <input value={manualSub} onChange={(e) => { setManualSub(e.target.value); setOfficerSub(""); }} placeholder="Paste an Officer-group user's sub" />
            <small className="ct-muted">The officer directory endpoint is not deployed yet; the backend still verifies Officer-group membership.</small>
          </label>
        )}
        <label className="ct-field">Note <span className="ct-muted">(optional, recorded in history)</span>
          <input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} placeholder={reassign ? "Reason for reassignment" : "Instructions for the officer"} />
        </label>
        {error && <p className="ct-error" role="alert">{error}</p>}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={busy}>{busy ? "Saving..." : reassign ? "Confirm reassignment" : "Confirm assignment"}</button>
        </div>
      </form>
    </div>
  );
}

function DetailsModal({ complaint, reference, onSubmit, onCancel }) {
  const managed = reference.wards.length > 0 || reference.zones.length > 0;
  const [form, setForm] = useState({
    priority: complaint.priority,
    departmentId: complaint.departmentId || reference.departments.find((d) => d.name === complaint.department)?.id || "",
    wardId: complaint.wardId || "", zoneId: complaint.zoneId || "", zone: complaint.zone || "", ward: complaint.ward || "",
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  async function submit(e) {
    e.preventDefault();
    const body = { priority: form.priority, ...(form.departmentId ? { departmentId: form.departmentId } : {}) };
    if (managed) Object.assign(body, { wardId: form.wardId, ...(form.wardId ? {} : { zoneId: form.zoneId }) });
    else Object.assign(body, { zone: form.zone.trim(), ward: form.ward.trim() });
    setBusy(true);
    try { await onSubmit(body); } catch (err) { setError(err.message); setBusy(false); }
  }

  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="details-title">
        <div className="ct-modal-head">
          <div><h3 id="details-title">Classify complaint</h3><p className="ct-muted">{complaint.id} · {complaint.title}</p></div>
          <button type="button" className="ct-icon-btn" onClick={onCancel} aria-label="Close"><X size={19} /></button>
        </div>
        <div className="ct-form-grid">
          <label className="ct-field">Priority<select autoFocus value={form.priority} onChange={set("priority")}>{PRIORITIES.map((p) => <option key={p}>{p}</option>)}</select></label>
          <label className="ct-field">Department<select value={form.departmentId} onChange={set("departmentId")}>{reference.departments.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
          {managed ? (
            <>
              <label className="ct-field">Ward<select value={form.wardId} onChange={set("wardId")}><option value="">— none —</option>{reference.wards.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}</select></label>
              <label className="ct-field">Zone {form.wardId && <span className="ct-muted">(from ward)</span>}<select value={form.zoneId} onChange={set("zoneId")} disabled={!!form.wardId}><option value="">— none —</option>{reference.zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}</select></label>
            </>
          ) : (
            <>
              <label className="ct-field">Zone<input value={form.zone} onChange={set("zone")} maxLength={80} /></label>
              <label className="ct-field">Ward<input value={form.ward} onChange={set("ward")} maxLength={80} /></label>
            </>
          )}
        </div>
        {!managed && <p className="ct-muted ct-small">Define zones and wards under “Zones & Wards” to choose from a managed list.</p>}
        {error && <p className="ct-error" role="alert">{error}</p>}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={busy}>{busy ? "Saving..." : "Save changes"}</button>
        </div>
      </form>
    </div>
  );
}

function RejectModal({ complaint, candidates, onSubmit, onCancel }) {
  const [reason, setReason] = useState("");
  const [duplicateOf, setDuplicateOf] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(e) {
    e.preventDefault();
    if (reason.trim().length < 10) { setError("Give a reason of at least 10 characters; the citizen will see it."); return; }
    setBusy(true);
    try { await onSubmit(reason.trim(), duplicateOf || undefined); } catch (err) { setError(err.message); setBusy(false); }
  }
  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="reject-title">
        <h3 id="reject-title">Close without action</h3>
        <p className="ct-muted">{complaint.id} · {complaint.title}. Use for duplicates, invalid or out-of-jurisdiction reports. This is final.</p>
        <label className="ct-field">Reason shown to the citizen<textarea autoFocus rows={3} maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        {candidates.length > 0 && (
          <label className="ct-field">Duplicate of (optional)<select value={duplicateOf} onChange={(e) => setDuplicateOf(e.target.value)}>
            <option value="">— not a duplicate —</option>
            {candidates.map(({ complaint: c, distanceKm }) => <option key={c.id} value={c.id}>{c.id} · {c.title} · {Math.round(distanceKm * 1000)} m</option>)}
          </select></label>
        )}
        {error && <p className="ct-error" role="alert">{error}</p>}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-danger" disabled={busy}><XCircle size={15} /> Reject complaint</button>
        </div>
      </form>
    </div>
  );
}

export default function AdminDashboard({ onLogout = () => {} }) {
  const auth = useAuthContext();
  const toast = useToast();
  const confirm = useConfirm();
  const now = useNow(60000);
  const { complaints: loaded, loading, error, reload, replace, loadedAt } = useComplaints(auth);
  const complaints = useMemo(() => loaded.map((c) => ({ ...c, sla: slaState(c, now) })), [loaded, now]);
  const { officers, source, error: officerError, reload: reloadOfficers } = useOfficers(auth, complaints);
  const { ref: reference, reload: reloadReference } = useReference(auth, { boundaries: true });
  const { feed, unread, markSeen } = useNotifications(auth, loadedAt);
  const [page, setPage] = useState("overview");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [selected, setSelected] = useState(null);
  const [assignTarget, setAssignTarget] = useState(null);
  const [detailsTarget, setDetailsTarget] = useState(null);
  const [rejectTarget, setRejectTarget] = useState(null);
  const filtered = useMemo(() => applyFilters(complaints, filters), [complaints, filters]);
  const s = summarize(complaints);

  const showComplaints = useCallback((patch) => { setFilters({ ...EMPTY_FILTERS, ...patch }); setPage("complaints"); }, []);

  const alerts = [
    ...complaints.filter((c) => c.sla.state === "breached").map((c) => ({ id: `o-${c.id}`, title: `SLA breached: ${c.id}`, detail: `${c.title} · ${c.assignedOfficerName || "unassigned"}`, tone: "danger", onClick: () => setSelected(c) })),
    ...complaints.filter((c) => c.sla.state === "warning").map((c) => ({ id: `w-${c.id}`, title: `Due soon: ${c.id}`, detail: c.title, tone: "warning", onClick: () => setSelected(c) })),
    ...complaints.filter((c) => c.status === "PENDING" || c.status === "REOPENED").map((c) => ({ id: `p-${c.id}`, title: `Needs assignment: ${c.id}`, detail: c.title, onClick: () => setSelected(c) })),
  ];

  async function doAssign(officerSub, reason, force) {
    const target = assignTarget;
    const data = await api.assign(auth, target.complaint.id, officerSub, reason, force);
    const updated = replace(data.complaint);
    target.update?.(updated);
    setAssignTarget(null);
    toast(data.message || "Complaint assigned.");
  }

  async function doUnassign(c, update) {
    const reason = await confirm({ title: `Unassign ${c.assignedOfficerName}?`, message: "The complaint returns to Pending and the officer loses access to it.", confirmLabel: "Unassign", tone: "danger", requireText: true, textLabel: "Reason (recorded in history)", textMinLength: 3 });
    if (!reason) return;
    try {
      const data = await api.unassign(auth, c.id, reason);
      update?.(replace(data.complaint));
      toast("Officer unassigned.");
    } catch (err) { toast(err.message, "error"); }
  }

  async function doEscalate(c, update) {
    const reason = await confirm({ title: `Escalate ${c.id}?`, message: "Raises the priority one level and emails the assigned officer and administrators.", confirmLabel: "Escalate", tone: "danger", requireText: true, textLabel: "Reason", textMinLength: 5 });
    if (!reason) return;
    try {
      const data = await api.escalate(auth, c.id, reason);
      update?.(replace(data.complaint));
      toast(data.message);
    } catch (err) { toast(err.message, "error"); }
  }

  async function doDetails(values) {
    const target = detailsTarget;
    const data = await api.updateDetails(auth, target.complaint.id, values);
    target.update?.(replace(data.complaint));
    setDetailsTarget(null);
    toast("Complaint details updated.");
  }

  async function doReject(reason, duplicateOf) {
    const target = rejectTarget;
    const data = await api.reject(auth, target.complaint.id, reason, duplicateOf);
    target.update?.(replace(data.complaint));
    setRejectTarget(null);
    toast("Complaint closed without action. The citizen has been notified.");
  }

  async function doClose(c, update) {
    const ok = await confirm({ title: "Close this complaint?", message: "Use this when the citizen has not responded and the resolution is verified. This cannot be undone.", confirmLabel: "Close complaint" });
    if (!ok) return;
    try {
      const data = await api.close(auth, c.id, { note: "Closed by administrator after verifying resolution" });
      update?.(replace(data.complaint));
      toast("Complaint closed.");
    } catch (err) { toast(err.message, "error"); }
  }

  const adminActions = (c, update) => (
    <>
      {(canTransition("assign", c.status) || canTransition("reassign", c.status)) && (
        <button type="button" className="ct-btn ct-btn-primary" onClick={() => setAssignTarget({ complaint: c, update })}>
          <UserPlus size={15} /> {c.status === "ASSIGNED" ? "Reassign" : "Assign officer"}
        </button>
      )}
      {canTransition("unassign", c.status) && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => doUnassign(c, update)}><UserMinus size={15} /> Unassign</button>}
      {!["REJECTED", "CLOSED"].includes(c.status) && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setDetailsTarget({ complaint: c, update })}><PencilLine size={15} /> Classify</button>}
      {canTransition("escalate", c.status) && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => doEscalate(c, update)}><ShieldAlert size={15} /> Escalate</button>}
      {canTransition("reject", c.status) && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setRejectTarget({ complaint: c, update })}><XCircle size={15} /> Reject</button>}
      {canTransition("close", c.status) && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => doClose(c, update)}><CheckCircle2 size={15} /> Close</button>}
    </>
  );

  const rowAction = (c) => (canTransition("assign", c.status) || canTransition("reassign", c.status)
    ? <button type="button" className="ct-btn ct-btn-sm" onClick={() => setAssignTarget({ complaint: c })}>{c.status === "ASSIGNED" ? "Reassign" : "Assign"}</button>
    : null);

  let body;
  if (loading && !loaded.length) body = <LoadingState label="Loading complaints..." />;
  else if (error) body = <ErrorState message={error} onRetry={reload} />;
  else if (page === "overview") {
    const needsAssignment = complaints.filter((c) => c.status === "PENDING" || c.status === "REOPENED");
    const atRisk = complaints.filter((c) => c.sla.state === "breached" || c.sla.state === "warning").sort((a, b) => a.sla.remainingMs - b.sla.remainingMs);
    body = (
      <>
        <div className="ct-stats">
          <StatCard label="Total complaints" value={s.total} icon={ClipboardList} onClick={() => showComplaints({})} />
          <StatCard label="Pending assignment" value={s.pending + s.reopened} note={s.reopened ? `${s.reopened} reopened` : undefined} icon={Clock3} tone="orange" onClick={() => showComplaints({ status: "PENDING" })} />
          <StatCard label="Assigned / in progress" value={s.assigned + s.inProgress} icon={TrendingUp} tone="purple" onClick={() => showComplaints({ status: "IN_PROGRESS" })} />
          <StatCard label="Resolved / closed" value={s.resolved + s.closed} icon={CheckCircle2} tone="green" onClick={() => showComplaints({ status: "RESOLVED" })} />
          <StatCard label="Overdue" value={s.overdue} icon={AlertTriangle} tone="red" onClick={() => showComplaints({ sla: "breached" })} />
          <StatCard label="SLA warnings" value={s.warning} note={`Due within ${reference.settings.warningHours} h`} icon={Clock3} tone="orange" onClick={() => showComplaints({ sla: "warning" })} />
        </div>
        <div className="ct-grid-2">
          <Panel title="Needs assignment" subtitle="Pending and reopened complaints">
            {needsAssignment.length ? <ComplaintTable complaints={needsAssignment.slice(0, 6)} onOpen={setSelected} columns={["id", "title", "priority", "sla"]} renderRowAction={rowAction} /> : <EmptyState title="All complaints are assigned" />}
          </Panel>
          <Panel title="SLA at risk" subtitle="Overdue or inside the warning window">
            {atRisk.length ? <ComplaintTable complaints={atRisk.slice(0, 6)} onOpen={setSelected} columns={["id", "title", "officer", "sla"]} /> : <EmptyState title="No SLA risks right now" />}
          </Panel>
        </div>
        <div className="ct-grid-2">
          <Panel title="Department workload" actions={<button type="button" className="ct-link" onClick={() => setPage("departments")}>Manage</button>}>
            <BarList rows={groupStats(complaints, (c) => c.department)} valueKey="open" format={(v) => `${v} open`} secondary={(r) => `${r.total} total · ${r.resolved} resolved · ${r.overdue} overdue`} />
          </Panel>
          <Panel title="Open complaints on the map" actions={<button type="button" className="ct-link" onClick={() => setPage("map")}>Open map</button>}>
            <ComplaintMap complaints={complaints.filter((c) => OPEN_STATUSES.has(c.status))} onSelect={setSelected} height={300} showModeSwitch={false} boundaries={reference.wards} />
          </Panel>
        </div>
      </>
    );
  } else if (page === "complaints") {
    body = (
      <Panel title="Complaint management" subtitle="Review, classify, assign, escalate and track every complaint">
        <FilterBar filters={filters} onChange={setFilters} complaints={complaints} fields={["status", "category", "department", "zone", "ward", "priority", "sla", "dates"]}
          resultCount={filtered.length} onExport={() => downloadFile(`civictrack-complaints-${stamp()}.csv`, toCsv(filtered))} />
        {filtered.length ? <ComplaintTable complaints={filtered} onOpen={setSelected} renderRowAction={rowAction} /> : <EmptyState title="No complaints match" message="Adjust or clear the filters." />}
      </Panel>
    );
  } else if (page === "map") {
    body = (
      <Panel title="City complaint map" subtitle="OpenStreetMap · marker letter = category, colour = status · ward shading = complaint count (when boundaries are imported)">
        <FilterBar filters={filters} onChange={setFilters} complaints={complaints} fields={["status", "category", "department", "zone", "ward", "priority", "sla", "dates"]} resultCount={filtered.length} />
        <ComplaintMap complaints={filtered} onSelect={setSelected} height={560} showSearch boundaries={reference.wards} />
      </Panel>
    );
  } else if (page === "analytics") {
    body = <AnalyticsView auth={auth} complaints={complaints} reference={reference} onOpen={setSelected} />;
  } else if (page === "officers") {
    body = <OfficersView auth={auth} reference={reference} officers={officers} source={source} officerError={officerError} reloadOfficers={reloadOfficers} showComplaints={showComplaints} />;
  } else if (page === "users") {
    body = <UsersView auth={auth} onChanged={reloadOfficers} />;
  } else if (page === "departments") {
    body = <DepartmentsView auth={auth} reference={reference} reloadReference={reloadReference} complaints={complaints} />;
  } else if (page === "areas") {
    body = <AreasView auth={auth} reference={reference} reloadReference={reloadReference} complaints={complaints} />;
  } else if (page === "settings") {
    body = <SettingsView key={JSON.stringify(reference.settings)} auth={auth} reference={reference} reloadReference={reloadReference} />;
  } else {
    body = <AuditView auth={auth} />;
  }

  return (
    <PortalShell portal="Administration" nav={NAV.map((n) => (n.id === "complaints" ? { ...n, count: s.pending + s.reopened } : n))} active={page} onNavigate={setPage}
      user={auth.user} roleLabel={auth.isDemo ? "Local demo admin" : "Administrator"} onLogout={onLogout} onRefresh={() => { reload(); reloadOfficers(); reloadReference(); }} refreshing={loading}
      alerts={alerts} feed={feed} unread={unread} onOpenFeed={markSeen} onSelectComplaint={(id) => setSelected(complaints.find((c) => c.id === id) || null)}>
      {body}
      {selected && <ComplaintDetail key={selected.id} complaint={selected} auth={auth} role="Admin" allComplaints={complaints} onOpenOther={setSelected} onClose={() => setSelected(null)} renderActions={adminActions} />}
      {assignTarget && <AssignModal complaint={assignTarget.complaint} officers={officers} departments={reference.departments} source={source} onSubmit={doAssign} onCancel={() => setAssignTarget(null)} />}
      {detailsTarget && <DetailsModal complaint={detailsTarget.complaint} reference={reference} onSubmit={doDetails} onCancel={() => setDetailsTarget(null)} />}
      {rejectTarget && <RejectModal complaint={rejectTarget.complaint} candidates={nearby(rejectTarget.complaint, complaints, 0.5).filter(({ complaint: c }) => c.status !== "REJECTED")} onSubmit={doReject} onCancel={() => setRejectTarget(null)} />}
    </PortalShell>
  );
}
