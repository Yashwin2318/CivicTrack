import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, ClipboardList, Clock3, Download, FileText, RotateCcw, TrendingUp, XCircle } from "lucide-react";
import ComplaintMap from "../components/ComplaintMap";
import { BarList, DonutStat, TrendChart } from "../components/Charts";
import { EmptyState, LoadingState, Panel, Select, StatCard } from "../components/ui";
import { api } from "../lib/api";
import { applyFilters, clientAggregate, csvCell, downloadFile, formatHours, formatPercent, toCsv } from "../lib/analytics";
import { CATEGORIES, PRIORITIES } from "../lib/lifecycle";

function PerformanceTable({ rows, label }) {
  if (!rows?.length) return <EmptyState title="No data" message="No complaints match the selected filters." />;
  return (
    <div className="ct-table-wrap">
      <table className="ct-table">
        <thead><tr><th>{label}</th><th>Total</th><th>Open</th><th>Resolved</th><th>Overdue</th><th>Avg. resolution</th><th>SLA met</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}><td><strong>{r.key}</strong></td><td>{r.total}</td><td>{r.open}</td><td>{r.resolved}</td>
              <td className={r.overdue ? "ct-text-danger" : ""}>{r.overdue}</td><td>{formatHours(r.avgResolutionHours)}</td><td>{formatPercent(r.slaComplianceRate ?? r.slaRate ?? null)}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AnalyticsView({ auth, complaints, reference, onOpen }) {
  const [filters, setFilters] = useState({ from: "", to: "", department: "ALL", zone: "ALL", ward: "ALL", category: "ALL", priority: "ALL" });
  const [granularity, setGranularity] = useState("day");
  const [server, setServer] = useState({ data: null, loading: true, error: "" });
  const set = (k) => (v) => setFilters((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    let cancelled = false;
    const params = { ...filters, granularity };
    api.analytics(auth, params)
      .then((data) => { if (!cancelled) setServer({ data: { ...data, source: "server" }, loading: false, error: "" }); })
      .catch((err) => { if (!cancelled) setServer({ data: null, loading: false, error: err.message }); });
    return () => { cancelled = true; };
  }, [auth, filters, granularity]);

  const fallback = useMemo(() => (server.data ? null : clientAggregate(complaints, filters, granularity)), [server.data, complaints, filters, granularity]);
  const data = server.data || fallback;
  const scoped = useMemo(() => applyFilters(complaints, filters), [complaints, filters]);
  const zones = [...new Set([...reference.zones.map((z) => z.name), ...complaints.map((c) => c.zone).filter(Boolean)])].sort();
  const wards = [...new Set([...reference.wards.map((w) => w.name), ...complaints.map((c) => c.ward).filter(Boolean)])].sort();
  const stamp = new Date().toISOString().slice(0, 10);

  if (server.loading && !data) return <LoadingState label="Aggregating complaint data..." />;
  const s = data.summary;
  const series = data.trend.map((t) => ({ key: t.period, label: t.period, created: t.created, resolved: t.resolved }));

  function exportSummary() {
    const lines = [["Metric", "Value"],
      ["Generated", new Date().toISOString()], ["Source", data.source === "server" ? "server aggregation" : "browser aggregation"],
      ["Filters", JSON.stringify(filters)], ["Total", s.total], ["New in period", s.newInPeriod], ["Open", s.open], ["Resolved/closed", s.resolved],
      ["Rejected", s.rejected], ["Overdue", s.overdue], ["Resolution rate", formatPercent(s.resolutionRate)],
      ["Average resolution hours", s.avgResolutionHours?.toFixed?.(2) ?? ""], ["Median resolution hours", s.medianResolutionHours?.toFixed?.(2) ?? ""],
      ["SLA compliance", formatPercent(s.slaComplianceRate)], ["Reopened (ever)", s.reopenedEver], ["Escalated", s.escalated], [],
      ["Group", "Name", "Total", "Open", "Resolved", "Overdue", "Avg resolution h", "SLA met"],
      ...[["Department", data.byDepartment], ["Category", data.byCategory], ["Priority", data.byPriority], ["Zone", data.byZone], ["Ward", data.byWard], ["Officer", data.byOfficer]]
        .flatMap(([g, rows]) => rows.map((r) => [g, r.key, r.total, r.open, r.resolved, r.overdue, r.avgResolutionHours?.toFixed?.(2) ?? "", formatPercent(r.slaComplianceRate ?? r.slaRate ?? null)]))];
    downloadFile(`civictrack-summary-${stamp}.csv`, lines.map((row) => row.map(csvCell).join(",")).join("\r\n"));
  }

  return (
    <div className="ct-report">
      <Panel title="Report filters" subtitle={data.source === "server" ? `Server-side aggregation over all stored complaints · generated ${new Date(data.generatedAt).toLocaleString("en-IN")}` : "Server analytics unavailable — computed in the browser from loaded complaints"}
        actions={<div className="ct-no-print ct-panel-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={exportSummary}><Download size={15} /> Summary CSV</button>
          <button type="button" className="ct-btn ct-btn-ghost" onClick={() => downloadFile(`civictrack-complaints-${stamp}.csv`, toCsv(scoped))}><Download size={15} /> Records CSV</button>
          <button type="button" className="ct-btn ct-btn-primary" onClick={() => window.print()}><FileText size={15} /> Print / save PDF</button>
        </div>}>
        {server.error && <div className="ct-banner ct-banner-info ct-no-print"><AlertTriangle size={16} /> {server.error}</div>}
        <div className="ct-filters ct-no-print">
          <label className="ct-select"><span>From</span><input type="date" value={filters.from} max={filters.to || undefined} onChange={(e) => set("from")(e.target.value)} /></label>
          <label className="ct-select"><span>To</span><input type="date" value={filters.to} min={filters.from || undefined} onChange={(e) => set("to")(e.target.value)} /></label>
          <Select label="Department" value={filters.department} onChange={set("department")} options={reference.departments.map((d) => d.name)} />
          <Select label="Zone" value={filters.zone} onChange={set("zone")} options={zones} />
          <Select label="Ward" value={filters.ward} onChange={set("ward")} options={wards} />
          <Select label="Category" value={filters.category} onChange={set("category")} options={reference.categories.map((c) => c.name) || CATEGORIES} />
          <Select label="Priority" value={filters.priority} onChange={set("priority")} options={PRIORITIES} />
        </div>
        <p className="ct-print-only ct-small">Filters: {Object.entries(filters).filter(([, v]) => v && v !== "ALL").map(([k, v]) => `${k}=${v}`).join(", ") || "none"}</p>
      </Panel>
      <div className="ct-stats">
        <StatCard label="Total complaints" value={s.total} note={`${s.newInPeriod} new ${filters.from ? "in period" : "in last 7 days"}`} icon={ClipboardList} />
        <StatCard label="Open" value={s.open} note={`${s.byStatus?.PENDING || 0} pending · ${s.byStatus?.IN_PROGRESS || 0} in progress`} icon={Clock3} tone="orange" />
        <StatCard label="Resolved / closed" value={s.resolved} note={`Resolution rate ${formatPercent(s.resolutionRate)}`} icon={CheckCircle2} tone="green" />
        <StatCard label="Overdue now" value={s.overdue} icon={AlertTriangle} tone="red" />
        <StatCard label="Rejected" value={s.rejected} icon={XCircle} />
        <StatCard label="Reopened (ever)" value={s.reopenedEver} icon={RotateCcw} tone="orange" />
        <StatCard label="Avg. / median resolution" value={formatHours(s.avgResolutionHours)} note={`Median ${formatHours(s.medianResolutionHours)}`} icon={TrendingUp} tone="purple" />
        <StatCard label="Escalated" value={s.escalated} icon={TrendingUp} tone="red" />
      </div>
      <div className="ct-grid-2">
        <Panel title="Complaint trend" actions={<div role="group" aria-label="Trend granularity" className="ct-no-print">{["day", "week", "month"].map((g) => <button key={g} type="button" className={`ct-chip ${granularity === g ? "active" : ""}`} onClick={() => setGranularity(g)} aria-pressed={granularity === g}>{g[0].toUpperCase() + g.slice(1)}</button>)}</div>}>
          <TrendChart data={series} />
        </Panel>
        <Panel title="SLA compliance" subtitle={`${s.slaMet ?? 0} of ${s.slaEvaluated ?? 0} finished complaints met their SLA`}>
          <div className="ct-donuts">
            <DonutStat value={s.slaComplianceRate} label="SLA compliance" />
            <DonutStat value={s.resolutionRate} label="Resolution rate" color="#2563eb" />
          </div>
        </Panel>
      </div>
      <div className="ct-grid-2">
        <Panel title="Category distribution"><BarList rows={data.byCategory} /></Panel>
        <Panel title="Priority distribution"><BarList rows={[...data.byPriority].sort((a, b) => PRIORITIES.indexOf(b.key) - PRIORITIES.indexOf(a.key))} color="#d97706" /></Panel>
      </div>
      <Panel title="Department performance"><PerformanceTable rows={data.byDepartment} label="Department" /></Panel>
      <Panel title="Officer performance"><PerformanceTable rows={data.byOfficer} label="Officer" /></Panel>
      <div className="ct-grid-2">
        <Panel title="Zone-wise complaints"><PerformanceTable rows={data.byZone} label="Zone" /></Panel>
        <Panel title="Ward-wise complaints"><PerformanceTable rows={data.byWard} label="Ward" /></Panel>
      </div>
      <Panel title="Complaint heatmap & hotspots" subtitle={`${data.mapped} complaint(s) with coordinates. Dashed rings mark hotspots (3+ complaints within ~500 m).`}>
        <ComplaintMap complaints={scoped} mode="density" onSelect={onOpen} height={400} hotspots={data.hotspots} boundaries={reference.wards} />
        {data.hotspots?.length > 0 && (
          <ul className="ct-small ct-mt">{data.hotspots.slice(0, 10).map((h) => <li key={`${h.latitude},${h.longitude}`}>{h.count} complaints near {h.latitude.toFixed(4)}, {h.longitude.toFixed(4)} — mostly {h.topCategory}</li>)}</ul>
        )}
      </Panel>
    </div>
  );
}
