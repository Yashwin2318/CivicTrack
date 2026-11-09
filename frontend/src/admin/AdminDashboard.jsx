import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, BarChart3, Building2, CheckCircle2, ClipboardList, Clock3, Download, LayoutDashboard, Map as MapIcon,
  PencilLine, RotateCcw, TrendingUp, UserMinus, UserPlus, Users, X,
} from "lucide-react";
import PortalShell from "../components/PortalShell";
import ComplaintDetail from "../components/ComplaintDetail";
import ComplaintMap from "../components/ComplaintMap";
import { BarList, DonutStat, TrendChart } from "../components/Charts";
import { useConfirm, useToast } from "../components/feedback";
import { ComplaintTable, EMPTY_FILTERS, FilterBar, useAuthContext, useComplaints } from "../components/complaints";
import { EmptyState, ErrorState, LoadingState, Panel, StatCard, useNow } from "../components/ui";
import { api } from "../lib/api";
import { applyFilters, downloadFile, formatHours, formatPercent, groupStats, average, resolutionHours, slaCompliance, summarize, toCsv, trend } from "../lib/analytics";
import { haversineKm, withCoords } from "../lib/geo";
import { DEPARTMENTS, OPEN_STATUSES, PRIORITIES, canTransition, slaState } from "../lib/lifecycle";

const NAV = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "complaints", label: "Complaints", icon: ClipboardList },
  { id: "map", label: "City Map", icon: MapIcon },
  { id: "officers", label: "Officers", icon: Users },
  { id: "areas", label: "Departments & Areas", icon: Building2 },
  { id: "analytics", label: "Analytics & Reports", icon: BarChart3 },
];

function stamp() {
  return new Date().toISOString().slice(0, 10);
}

/** Officer directory from Cognito; falls back to officers seen in assignments
 * when the /officers endpoint is not yet deployed. */
function useOfficers(auth, complaints) {
  const [state, setState] = useState({ officers: [], source: "loading", error: "" });
  useEffect(() => {
    let cancelled = false;
    api.listOfficers(auth)
      .then((data) => { if (!cancelled) setState({ officers: data.officers || [], source: data.source || "cognito", error: "" }); })
      .catch((err) => { if (!cancelled) setState({ officers: [], source: "fallback", error: err.message }); });
    return () => { cancelled = true; };
  }, [auth]);

  return useMemo(() => {
    const base = state.source === "fallback"
      ? [...new Map(complaints.filter((c) => c.assignedOfficerSub).map((c) => [c.assignedOfficerSub, { sub: c.assignedOfficerSub, name: c.assignedOfficerName, email: "", enabled: true }])).values()]
      : state.officers;
    const withLoad = base.map((o) => {
      const mine = complaints.filter((c) => c.assignedOfficerSub === o.sub);
      const active = mine.filter((c) => OPEN_STATUSES.has(c.status));
      const done = mine.filter((c) => !OPEN_STATUSES.has(c.status));
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
    });
    return { officers: withLoad.sort((a, b) => a.displayName.localeCompare(b.displayName)), source: state.source, error: state.error };
  }, [state, complaints]);
}

function AssignModal({ complaint, officers, source, onSubmit, onCancel }) {
  const [officerSub, setOfficerSub] = useState("");
  const [manualSub, setManualSub] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reassign = complaint.status === "ASSIGNED";
  const candidates = officers.filter((o) => o.enabled !== false && o.sub !== (reassign ? complaint.assignedOfficerSub : null));

  // Location-based hint: officers already working near this complaint.
  const nearbyLoad = (o) => (complaint.hasCoords ? withCoords(o.activeComplaints).filter((c) => haversineKm(complaint, c) <= 2).length : 0);

  async function submit(e) {
    e.preventDefault();
    const sub = officerSub || manualSub.trim();
    if (!sub) { setError("Choose an officer."); return; }
    setBusy(true);
    setError("");
    try { await onSubmit(sub, reason.trim()); } catch (err) { setError(err.message); setBusy(false); }
  }

  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="assign-title">
        <div className="ct-modal-head">
          <div><h3 id="assign-title">{reassign ? "Reassign complaint" : "Assign complaint"}</h3><p className="ct-muted">{complaint.id} · {complaint.title} · {complaint.department}</p></div>
          <button type="button" className="ct-icon-btn" onClick={onCancel} aria-label="Close"><X size={19} /></button>
        </div>
        {reassign && <p className="ct-small">Currently assigned to <strong>{complaint.assignedOfficerName}</strong>. Reassignment is only possible before work starts.</p>}
        {candidates.length > 0 ? (
          <fieldset className="ct-officer-pick">
            <legend>Select officer</legend>
            {candidates.map((o) => (
              <label key={o.sub} className={`ct-officer-option ${officerSub === o.sub ? "active" : ""}`}>
                <input type="radio" name="officer" value={o.sub} checked={officerSub === o.sub} onChange={() => setOfficerSub(o.sub)} />
                <span><strong>{o.displayName}</strong>{o.email && <small className="ct-muted"> · {o.email}</small>}</span>
                <span className="ct-small ct-muted">{o.active} active{o.overdue ? ` · ${o.overdue} overdue` : ""}{nearbyLoad(o) ? ` · ${nearbyLoad(o)} within 2 km` : ""}</span>
              </label>
            ))}
          </fieldset>
        ) : <p className="ct-muted">No officers available in the directory.</p>}
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

function DetailsModal({ complaint, zones, wards, onSubmit, onCancel }) {
  const [form, setForm] = useState({ priority: complaint.priority, department: complaint.department, zone: complaint.zone || "", ward: complaint.ward || "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  async function submit(e) {
    e.preventDefault();
    if (form.zone.length > 80 || form.ward.length > 80) { setError("Zone and ward must be 80 characters or fewer."); return; }
    setBusy(true);
    try { await onSubmit({ ...form, zone: form.zone.trim(), ward: form.ward.trim() }); } catch (err) { setError(err.message); setBusy(false); }
  }
  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal" onSubmit={submit} role="dialog" aria-modal="true" aria-labelledby="details-title">
        <div className="ct-modal-head">
          <div><h3 id="details-title">Edit classification</h3><p className="ct-muted">{complaint.id} · {complaint.title}</p></div>
          <button type="button" className="ct-icon-btn" onClick={onCancel} aria-label="Close"><X size={19} /></button>
        </div>
        <div className="ct-form-grid">
          <label className="ct-field">Priority<select value={form.priority} onChange={set("priority")}>{PRIORITIES.map((p) => <option key={p}>{p}</option>)}</select></label>
          <label className="ct-field">Department<select value={form.department} onChange={set("department")}>{DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}</select></label>
          <label className="ct-field">Zone<input list="ct-zones" value={form.zone} onChange={set("zone")} maxLength={80} placeholder="e.g. East" /></label>
          <label className="ct-field">Ward<input list="ct-wards" value={form.ward} onChange={set("ward")} maxLength={80} placeholder="e.g. Ward 80 – Hoysala Nagar" /></label>
          <datalist id="ct-zones">{zones.map((z) => <option key={z} value={z} />)}</datalist>
          <datalist id="ct-wards">{wards.map((w) => <option key={w} value={w} />)}</datalist>
        </div>
        <p className="ct-muted ct-small">Zones and wards are entered by administrators. No boundary data is assumed.</p>
        {error && <p className="ct-error" role="alert">{error}</p>}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={busy}>{busy ? "Saving..." : "Save changes"}</button>
        </div>
      </form>
    </div>
  );
}

function PerformanceTable({ rows, label }) {
  if (!rows.length) return <EmptyState title="No data" message="No complaints match the selected period." />;
  return (
    <div className="ct-table-wrap">
      <table className="ct-table">
        <thead><tr><th>{label}</th><th>Total</th><th>Open</th><th>Resolved</th><th>Overdue</th><th>Avg. resolution</th><th>SLA met</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}><td><strong>{r.key}</strong></td><td>{r.total}</td><td>{r.open}</td><td>{r.resolved}</td>
              <td className={r.overdue ? "ct-text-danger" : ""}>{r.overdue}</td><td>{formatHours(r.avgResolutionHours)}</td><td>{formatPercent(r.slaRate)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Analytics({ complaints, onOpen }) {
  const [range, setRange] = useState({ ...EMPTY_FILTERS });
  const [granularity, setGranularity] = useState("day");
  const scoped = useMemo(() => applyFilters(complaints, range), [complaints, range]);
  const s = summarize(scoped);
  const compliance = slaCompliance(scoped);
  const avgResolution = average(scoped.map(resolutionHours));
  const series = useMemo(() => trend(scoped, granularity), [scoped, granularity]);
  const officerRows = groupStats(scoped.filter((c) => c.assignedOfficerName), (c) => c.assignedOfficerName);

  function exportSummary() {
    const sections = [
      ["Metric", "Value"],
      ["Period from", range.from || "all"], ["Period to", range.to || "all"],
      ["Total complaints", s.total], ["Open", s.open], ["Resolved", s.resolved], ["Closed", s.closed], ["Overdue (open, past SLA)", s.overdue],
      ["Ever reopened", s.everReopened], ["Escalated", s.escalated], ["Average resolution hours", avgResolution === null ? "" : avgResolution.toFixed(2)],
      ["SLA compliance", compliance.rate === null ? "" : `${(compliance.rate * 100).toFixed(1)}%`], ["SLA evaluated", compliance.evaluated],
      [], ["Group", "Name", "Total", "Open", "Resolved", "Overdue", "Avg resolution h", "SLA met %"],
      ...[["Department", groupStats(scoped, (c) => c.department)], ["Zone", groupStats(scoped, (c) => c.zone)], ["Ward", groupStats(scoped, (c) => c.ward)], ["Category", groupStats(scoped, (c) => c.category)], ["Officer", officerRows]]
        .flatMap(([g, rows]) => rows.map((r) => [g, r.key, r.total, r.open, r.resolved, r.overdue, r.avgResolutionHours?.toFixed(2) ?? "", r.slaRate === null ? "" : (r.slaRate * 100).toFixed(1)])),
    ];
    const csv = sections.map((row) => row.map((v) => (/[",\n]/.test(String(v ?? "")) ? `"${String(v).replace(/"/g, '""')}"` : v ?? "")).join(",")).join("\r\n");
    downloadFile(`civictrack-summary-${stamp()}.csv`, csv);
  }

  return (
    <>
      <Panel title="Report period" subtitle="All figures are computed from stored complaint records."
        actions={<>
          <button type="button" className="ct-btn ct-btn-ghost" onClick={exportSummary}><Download size={15} /> Summary CSV</button>
          <button type="button" className="ct-btn ct-btn-ghost" onClick={() => downloadFile(`civictrack-complaints-${stamp()}.csv`, toCsv(scoped))}><Download size={15} /> Records CSV</button>
        </>}>
        <FilterBar filters={range} onChange={setRange} complaints={complaints} fields={["department", "zone", "ward", "dates"]} resultCount={scoped.length} />
      </Panel>
      <div className="ct-stats">
        <StatCard label="Complaints" value={s.total} icon={ClipboardList} />
        <StatCard label="Avg. resolution time" value={formatHours(avgResolution)} icon={Clock3} tone="purple" />
        <StatCard label="Overdue now" value={s.overdue} icon={AlertTriangle} tone="red" />
        <StatCard label="Reopened (ever)" value={s.everReopened} icon={RotateCcw} tone="orange" />
        <StatCard label="Escalations" value={s.escalated} icon={TrendingUp} tone="red" />
      </div>
      <div className="ct-grid-2">
        <Panel title="Complaint trend" actions={
          <div role="group" aria-label="Trend granularity">{["day", "week", "month"].map((g) => <button key={g} type="button" className={`ct-chip ${granularity === g ? "active" : ""}`} onClick={() => setGranularity(g)} aria-pressed={granularity === g}>{g[0].toUpperCase() + g.slice(1)}</button>)}</div>
        }>
          <TrendChart data={series} />
        </Panel>
        <Panel title="SLA compliance" subtitle="Resolved/closed complaints finished within their SLA">
          <div className="ct-donuts">
            <DonutStat value={compliance.rate} label={`${compliance.met} met · ${compliance.missed} missed`} />
            <DonutStat value={s.total ? (s.resolved + s.closed) / s.total : null} label="Resolution rate" color="#2563eb" />
          </div>
        </Panel>
      </div>
      <div className="ct-grid-2">
        <Panel title="Category distribution"><BarList rows={groupStats(scoped, (c) => c.category)} /></Panel>
        <Panel title="Officer performance"><BarList rows={officerRows} valueKey="resolved" format={(v) => `${v} resolved`} color="#16a34a" secondary={(r) => `${r.total} assigned · avg ${formatHours(r.avgResolutionHours)} · SLA ${formatPercent(r.slaRate)}`} /></Panel>
      </div>
      <Panel title="Department performance"><PerformanceTable rows={groupStats(scoped, (c) => c.department)} label="Department" /></Panel>
      <div className="ct-grid-2">
        <Panel title="Zone performance"><PerformanceTable rows={groupStats(scoped, (c) => c.zone)} label="Zone" /></Panel>
        <Panel title="Ward performance"><PerformanceTable rows={groupStats(scoped, (c) => c.ward)} label="Ward" /></Panel>
      </div>
      <Panel title="Complaint heatmap" subtitle="Density of complaints with map coordinates in the selected period">
        <ComplaintMap complaints={scoped} mode="density" onSelect={onOpen} height={380} />
      </Panel>
    </>
  );
}

export default function AdminDashboard({ onLogout = () => {} }) {
  const auth = useAuthContext();
  const toast = useToast();
  const confirm = useConfirm();
  const now = useNow(60000);
  const { complaints: loaded, loading, error, reload, replace } = useComplaints(auth);
  const complaints = useMemo(() => loaded.map((c) => ({ ...c, sla: slaState(c, now) })), [loaded, now]);
  const { officers, source, error: officerError } = useOfficers(auth, complaints);
  const [page, setPage] = useState("overview");
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [selected, setSelected] = useState(null);
  const [assignTarget, setAssignTarget] = useState(null);
  const [detailsTarget, setDetailsTarget] = useState(null);
  const filtered = useMemo(() => applyFilters(complaints, filters), [complaints, filters]);
  const s = summarize(complaints);
  const zones = [...new Set(complaints.map((c) => c.zone).filter(Boolean))].sort();
  const wards = [...new Set(complaints.map((c) => c.ward).filter(Boolean))].sort();

  const showComplaints = useCallback((patch) => { setFilters({ ...EMPTY_FILTERS, ...patch }); setPage("complaints"); }, []);

  const alerts = [
    ...complaints.filter((c) => c.sla.state === "breached").map((c) => ({ id: `o-${c.id}`, title: `SLA breached: ${c.id}`, detail: `${c.title} · ${c.assignedOfficerName || "unassigned"}`, tone: "danger", onClick: () => setSelected(c) })),
    ...complaints.filter((c) => c.sla.state === "warning").map((c) => ({ id: `w-${c.id}`, title: `Due within 4 h: ${c.id}`, detail: c.title, tone: "warning", onClick: () => setSelected(c) })),
    ...complaints.filter((c) => c.status === "PENDING" || c.status === "REOPENED").map((c) => ({ id: `p-${c.id}`, title: `Needs assignment: ${c.id}`, detail: c.title, onClick: () => setSelected(c) })),
  ];

  async function doAssign(officerSub, reason) {
    const target = assignTarget;
    const data = await api.assign(auth, target.complaint.id, officerSub, reason);
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

  async function doDetails(values) {
    const target = detailsTarget;
    const data = await api.updateDetails(auth, target.complaint.id, values);
    target.update?.(replace(data.complaint));
    setDetailsTarget(null);
    toast("Complaint details updated.");
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
      <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setDetailsTarget({ complaint: c, update })}><PencilLine size={15} /> Priority / area</button>
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
          <StatCard label="SLA warnings" value={s.warning} note="Due within 4 h" icon={Clock3} tone="orange" onClick={() => showComplaints({ sla: "warning" })} />
        </div>
        <div className="ct-grid-2">
          <Panel title="Needs assignment" subtitle="Pending and reopened complaints">
            {needsAssignment.length ? <ComplaintTable complaints={needsAssignment.slice(0, 6)} onOpen={setSelected} columns={["id", "title", "sla"]} renderRowAction={rowAction} /> : <EmptyState title="All complaints are assigned" />}
          </Panel>
          <Panel title="SLA at risk" subtitle="Overdue or due within 4 hours">
            {atRisk.length ? <ComplaintTable complaints={atRisk.slice(0, 6)} onOpen={setSelected} columns={["id", "title", "officer", "sla"]} /> : <EmptyState title="No SLA risks right now" />}
          </Panel>
        </div>
        <div className="ct-grid-2">
          <Panel title="Department performance" actions={<button type="button" className="ct-link" onClick={() => setPage("areas")}>Details</button>}>
            <BarList rows={groupStats(complaints, (c) => c.department)} valueKey="resolved" format={(v) => `${v} resolved`} secondary={(r) => `${r.total} total · ${r.open} open · ${r.overdue} overdue`} />
          </Panel>
          <Panel title="Complaint locations" actions={<button type="button" className="ct-link" onClick={() => setPage("map")}>Open map</button>}>
            <ComplaintMap complaints={complaints.filter((c) => OPEN_STATUSES.has(c.status))} onSelect={setSelected} height={300} showModeSwitch={false} />
          </Panel>
        </div>
      </>
    );
  } else if (page === "complaints") {
    body = (
      <Panel title="Complaint management" subtitle="Review, classify, assign and track every complaint">
        <FilterBar filters={filters} onChange={setFilters} complaints={complaints} resultCount={filtered.length} onExport={() => downloadFile(`civictrack-complaints-${stamp()}.csv`, toCsv(filtered))} />
        {filtered.length ? <ComplaintTable complaints={filtered} onOpen={setSelected} renderRowAction={rowAction} /> : <EmptyState title="No complaints match" message="Adjust or clear the filters." />}
      </Panel>
    );
  } else if (page === "map") {
    const mapped = filtered;
    body = (
      <Panel title="City complaint map" subtitle="OpenStreetMap · filter by status, department, zone or ward">
        <FilterBar filters={filters} onChange={setFilters} complaints={complaints} fields={["status", "department", "zone", "ward", "sla"]} resultCount={mapped.length} />
        <ComplaintMap complaints={mapped} onSelect={setSelected} height={560} />
      </Panel>
    );
  } else if (page === "officers") {
    body = (
      <Panel title="Officer directory & workload" subtitle={source === "cognito" ? "Members of the Cognito Officer group" : source === "local-demo" ? "Local demo officers" : "Officers referenced by assignments"}>
        {source === "fallback" && <div className="ct-banner ct-banner-info"><AlertTriangle size={16} /> Officer directory unavailable ({officerError}). Showing officers from existing assignments until the new API route is deployed.</div>}
        {officers.length ? (
          <div className="ct-table-wrap">
            <table className="ct-table">
              <thead><tr><th>Officer</th><th>Status</th><th>Active</th><th>Overdue</th><th>Completed</th><th>Avg. resolution</th><th>SLA met</th></tr></thead>
              <tbody>
                {officers.map((o) => (
                  <tr key={o.sub}>
                    <td className="ct-cell-main"><strong>{o.displayName}</strong><span className="ct-muted ct-small">{o.email}</span></td>
                    <td>{o.enabled === false ? <span className="ct-badge ct-status-closed">Disabled</span> : <span className="ct-badge ct-status-resolved">Active</span>}</td>
                    <td><button type="button" className="ct-link" onClick={() => showComplaints({ search: o.displayName })}>{o.active}</button></td>
                    <td className={o.overdue ? "ct-text-danger" : ""}>{o.overdue}</td>
                    <td>{o.completed}</td><td>{formatHours(o.avgResolutionHours)}</td><td>{formatPercent(o.slaRate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <EmptyState title="No officers found" message="Add users to the Cognito Officer group to make them assignable." />}
        <p className="ct-muted ct-small ct-mt">Officer accounts are managed in Amazon Cognito (Officer group). CivicTrack never creates or deletes identities.</p>
      </Panel>
    );
  } else if (page === "areas") {
    const zoneRows = groupStats(complaints, (c) => c.zone);
    body = (
      <>
        <Panel title="Departments" subtitle="Category → department mapping; administrators can override per complaint">
          <PerformanceTable rows={groupStats(complaints, (c) => c.department)} label="Department" />
        </Panel>
        <Panel title="Zone → ward hierarchy" subtitle="Built from zone/ward values administrators assign to complaints. Boundaries are not assumed.">
          {zoneRows.map((z) => {
            const wardRows = groupStats(complaints.filter((c) => (c.zone || "Unspecified") === z.key), (c) => c.ward);
            return (
              <details key={z.key} className="ct-tree" open={zoneRows.length <= 3}>
                <summary><strong>{z.key}</strong> <span className="ct-muted">· {z.total} complaints · {z.open} open · {z.overdue} overdue · SLA {formatPercent(z.slaRate)}</span></summary>
                <PerformanceTable rows={wardRows} label="Ward" />
              </details>
            );
          })}
          {s.total > 0 && !zones.length && <p className="ct-muted">No zones assigned yet. Open a complaint and use “Priority / area” to classify it.</p>}
        </Panel>
      </>
    );
  } else {
    body = <Analytics complaints={complaints} onOpen={setSelected} />;
  }

  return (
    <PortalShell portal="Administration" nav={NAV.map((n) => (n.id === "complaints" ? { ...n, count: s.pending + s.reopened } : n))} active={page} onNavigate={setPage} user={auth.user} roleLabel={auth.isDemo ? "Local demo admin" : "Administrator"} onLogout={onLogout} onRefresh={reload} refreshing={loading} alerts={alerts}>
      {body}
      {selected && <ComplaintDetail key={selected.id} complaint={selected} auth={auth} role="Admin" allComplaints={complaints} onOpenOther={setSelected} onClose={() => setSelected(null)} renderActions={adminActions} />}
      {assignTarget && <AssignModal complaint={assignTarget.complaint} officers={officers} source={source} onSubmit={doAssign} onCancel={() => setAssignTarget(null)} />}
      {detailsTarget && <DetailsModal complaint={detailsTarget.complaint} zones={zones} wards={wards} onSubmit={doDetails} onCancel={() => setDetailsTarget(null)} />}
    </PortalShell>
  );
}
