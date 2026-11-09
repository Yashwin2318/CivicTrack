/* eslint-disable react-refresh/only-export-components */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Download, Search, X } from "lucide-react";
import { useAuth } from "../auth/AuthProvider";
import { api } from "../lib/api";
import { CATEGORIES, DEPARTMENTS, PRIORITIES, STATUS_LABELS, STATUS_ORDER, normalizeComplaint } from "../lib/lifecycle";
import { distinctValues } from "../lib/analytics";
import { PriorityBadge, Select, SlaBadge, StatusBadge, formatDate } from "./ui";

export function useAuthContext() {
  const { user, session, role, isDemo } = useAuth();
  return useMemo(() => ({ user, session, role, isDemo }), [user, session, role, isDemo]);
}

/** Loads the caller's complaints (the backend scopes them by role). */
export function useComplaints(auth) {
  const [state, setState] = useState({ complaints: [], loading: true, error: "", loadedAt: 0 });
  const mounted = useRef(true);

  const reload = useCallback(async ({ quiet = false } = {}) => {
    if (!quiet) setState((s) => ({ ...s, loading: true, error: "" }));
    try {
      const data = await api.listComplaints(auth);
      const now = Date.now();
      if (mounted.current) setState({ complaints: (data.complaints || []).map((c) => normalizeComplaint(c, now)), loading: false, error: "", loadedAt: now });
    } catch (err) {
      if (mounted.current) setState((s) => ({ ...s, loading: false, error: err.message || "Unable to load complaints." }));
    }
  }, [auth]);

  useEffect(() => {
    mounted.current = true;
    const run = async () => { await reload(); };
    run();
    return () => { mounted.current = false; };
  }, [reload]);

  const replace = useCallback((raw) => {
    const updated = normalizeComplaint(raw);
    setState((s) => ({ ...s, complaints: [updated, ...s.complaints.filter((c) => c.id !== updated.id)].sort((a, b) => (b.createdAt || "").localeCompare(a.createdAt || "")) }));
    return updated;
  }, []);

  return { ...state, reload, replace };
}

export const EMPTY_FILTERS = { search: "", status: "ALL", department: "ALL", category: "ALL", zone: "ALL", ward: "ALL", priority: "ALL", sla: "ALL", from: "", to: "" };

export function FilterBar({ filters, onChange, complaints, fields = ["status", "department", "zone", "ward", "priority", "sla", "dates"], onExport, resultCount }) {
  const set = (key) => (value) => onChange({ ...filters, [key]: value });
  const active = Object.entries(filters).some(([k, v]) => v && v !== "ALL" && EMPTY_FILTERS[k] !== v);
  return (
    <div className="ct-filters">
      <label className="ct-search">
        <Search size={17} aria-hidden="true" />
        <input value={filters.search} onChange={(e) => set("search")(e.target.value)} placeholder="Search ID, title, location..." aria-label="Search complaints" />
      </label>
      {fields.includes("status") && <Select label="Status" value={filters.status} onChange={set("status")} options={STATUS_ORDER.map((s) => ({ value: s, label: STATUS_LABELS[s] }))} />}
      {fields.includes("category") && <Select label="Category" value={filters.category} onChange={set("category")} options={CATEGORIES} />}
      {fields.includes("department") && <Select label="Department" value={filters.department} onChange={set("department")} options={DEPARTMENTS} />}
      {fields.includes("zone") && <Select label="Zone" value={filters.zone} onChange={set("zone")} options={distinctValues(complaints, "zone")} />}
      {fields.includes("ward") && <Select label="Ward" value={filters.ward} onChange={set("ward")} options={distinctValues(complaints, "ward")} />}
      {fields.includes("priority") && <Select label="Priority" value={filters.priority} onChange={set("priority")} options={PRIORITIES} />}
      {fields.includes("sla") && <Select label="SLA" value={filters.sla} onChange={set("sla")} options={[
        { value: "breached", label: "Overdue" }, { value: "warning", label: "Due soon" }, { value: "on_track", label: "On track" }, { value: "met", label: "Met" }, { value: "missed", label: "Missed" },
      ]} />}
      {fields.includes("dates") && (
        <>
          <label className="ct-select"><span>From</span><input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => set("from")(e.target.value)} /></label>
          <label className="ct-select"><span>To</span><input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => set("to")(e.target.value)} /></label>
        </>
      )}
      <div className="ct-filters-end">
        {resultCount !== undefined && <span className="ct-muted">{resultCount} result{resultCount === 1 ? "" : "s"}</span>}
        {active && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => onChange(EMPTY_FILTERS)}><X size={15} /> Clear</button>}
        {onExport && <button type="button" className="ct-btn ct-btn-ghost" onClick={onExport}><Download size={15} /> Export CSV</button>}
      </div>
    </div>
  );
}

export function ComplaintTable({ complaints, onOpen, columns = ["id", "title", "area", "status", "priority", "sla", "officer", "date"], renderRowAction }) {
  return (
    <div className="ct-table-wrap">
      <table className="ct-table">
        <thead>
          <tr>
            {columns.includes("id") && <th>ID</th>}
            {columns.includes("title") && <th>Complaint</th>}
            {columns.includes("area") && <th>Zone / Ward</th>}
            {columns.includes("status") && <th>Status</th>}
            {columns.includes("priority") && <th>Priority</th>}
            {columns.includes("sla") && <th>SLA</th>}
            {columns.includes("officer") && <th>Officer</th>}
            {columns.includes("date") && <th>Submitted</th>}
            {renderRowAction && <th><span className="ct-sr">Actions</span></th>}
          </tr>
        </thead>
        <tbody>
          {complaints.map((c) => (
            <tr key={c.id} className={c.sla?.state === "breached" ? "ct-row-overdue" : ""}>
              {columns.includes("id") && <td><button type="button" className="ct-link" onClick={() => onOpen(c)}>{c.id}</button></td>}
              {columns.includes("title") && (
                <td className="ct-cell-main">
                  <button type="button" className="ct-row-title" onClick={() => onOpen(c)}>{c.title}</button>
                  <span className="ct-muted ct-small">{c.category} · {c.location}</span>
                </td>
              )}
              {columns.includes("area") && <td>{c.zone || "—"} / {c.ward || "—"}</td>}
              {columns.includes("status") && <td><StatusBadge status={c.status} /></td>}
              {columns.includes("priority") && <td><PriorityBadge priority={c.priority} /></td>}
              {columns.includes("sla") && <td><SlaBadge sla={c.sla} /></td>}
              {columns.includes("officer") && <td>{c.assignedOfficerName || <span className="ct-muted">Unassigned</span>}</td>}
              {columns.includes("date") && <td>{formatDate(c.createdDate)}</td>}
              {renderRowAction && <td className="ct-cell-actions">{renderRowAction(c)}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ComplaintCards({ complaints, onOpen, renderAction }) {
  return (
    <ul className="ct-cards">
      {complaints.map((c) => (
        <li key={c.id} className={`ct-card ${c.sla?.state === "breached" ? "ct-card-overdue" : ""}`}>
          <button type="button" className="ct-card-main" onClick={() => onOpen(c)}>
            <span className="ct-card-top"><strong>{c.title}</strong><StatusBadge status={c.status} /></span>
            <span className="ct-muted ct-small">{c.id} · {c.category} · {formatDate(c.createdDate)}</span>
            <span className="ct-small">{c.location}</span>
            <span className="ct-badges"><SlaBadge sla={c.sla} />{c.assignedOfficerName && <span className="ct-muted ct-small">Officer: {c.assignedOfficerName}</span>}</span>
          </button>
          {renderAction && <div className="ct-card-actions">{renderAction(c)}</div>}
        </li>
      ))}
    </ul>
  );
}
