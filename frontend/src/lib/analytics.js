// Aggregations computed only from stored complaint records — no synthetic data.
import { FINAL_STATUSES, OPEN_STATUSES } from "./lifecycle";

const HOUR = 3600000;
const DAY = 24 * HOUR;

export function summarize(complaints) {
  const count = (fn) => complaints.filter(fn).length;
  return {
    total: complaints.length,
    pending: count((c) => c.status === "PENDING"),
    assigned: count((c) => c.status === "ASSIGNED"),
    inProgress: count((c) => c.status === "IN_PROGRESS"),
    reopened: count((c) => c.status === "REOPENED"),
    resolved: count((c) => c.status === "RESOLVED"),
    closed: count((c) => c.status === "CLOSED"),
    open: count((c) => OPEN_STATUSES.has(c.status)),
    overdue: count((c) => c.sla?.state === "breached"),
    warning: count((c) => c.sla?.state === "warning"),
    everReopened: count((c) => c.reopenCount > 0),
    escalated: count((c) => c.escalated === true),
  };
}

export function resolutionHours(c) {
  if (!c.createdDate || !c.resolvedDate) return null;
  const hours = (c.resolvedDate.getTime() - c.createdDate.getTime()) / HOUR;
  return hours >= 0 ? hours : null;
}

export function average(values) {
  const valid = values.filter((v) => typeof v === "number" && Number.isFinite(v));
  return valid.length ? valid.reduce((a, b) => a + b, 0) / valid.length : null;
}

/** SLA compliance among complaints that have been resolved/closed. */
export function slaCompliance(complaints) {
  const finished = complaints.filter((c) => FINAL_STATUSES.has(c.status) && (c.sla?.state === "met" || c.sla?.state === "missed"));
  const met = finished.filter((c) => c.sla.state === "met").length;
  return { met, missed: finished.length - met, evaluated: finished.length, rate: finished.length ? met / finished.length : null };
}

export function groupStats(complaints, keyFn) {
  const groups = new Map();
  for (const c of complaints) {
    const key = keyFn(c) || "Unspecified";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(c);
  }
  return [...groups.entries()]
    .map(([key, items]) => {
      const compliance = slaCompliance(items);
      return {
        key,
        total: items.length,
        open: items.filter((c) => OPEN_STATUSES.has(c.status)).length,
        resolved: items.filter((c) => FINAL_STATUSES.has(c.status)).length,
        overdue: items.filter((c) => c.sla?.state === "breached").length,
        avgResolutionHours: average(items.map(resolutionHours)),
        slaRate: compliance.rate,
      };
    })
    .sort((a, b) => b.total - a.total || a.key.localeCompare(b.key));
}

function startOfBucket(date, granularity) {
  const d = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  if (granularity === "week") d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // Monday
  if (granularity === "month") d.setDate(1);
  return d;
}

function nextBucket(date, granularity) {
  const d = new Date(date);
  if (granularity === "day") d.setDate(d.getDate() + 1);
  else if (granularity === "week") d.setDate(d.getDate() + 7);
  else d.setMonth(d.getMonth() + 1);
  return d;
}

function bucketLabel(date, granularity) {
  if (granularity === "month") return date.toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
  return date.toLocaleDateString("en-IN", { day: "2-digit", month: "short" });
}

/** Created vs resolved per day/week/month, gap-filled, capped to maxBuckets. */
export function trend(complaints, granularity = "day", maxBuckets = 60) {
  const dates = complaints.flatMap((c) => [c.createdDate, c.resolvedDate]).filter(Boolean);
  if (!dates.length) return [];
  const min = startOfBucket(new Date(Math.min(...dates)), granularity);
  const max = startOfBucket(new Date(Math.max(...dates)), granularity);
  const buckets = [];
  for (let d = min; d <= max; d = nextBucket(d, granularity)) {
    buckets.push({ key: d.toISOString(), start: d, label: bucketLabel(d, granularity), created: 0, resolved: 0 });
  }
  const index = new Map(buckets.map((b) => [b.start.getTime(), b]));
  for (const c of complaints) {
    if (c.createdDate) index.get(startOfBucket(c.createdDate, granularity).getTime()).created += 1;
    if (c.resolvedDate) index.get(startOfBucket(c.resolvedDate, granularity).getTime()).resolved += 1;
  }
  return buckets.slice(-maxBuckets);
}

export function applyFilters(complaints, filters = {}) {
  const query = (filters.search || "").trim().toLowerCase();
  const from = filters.from ? new Date(`${filters.from}T00:00:00`) : null;
  const to = filters.to ? new Date(`${filters.to}T23:59:59.999`) : null;
  return complaints.filter((c) => {
    if (query && !`${c.id} ${c.title} ${c.category} ${c.location} ${c.ward} ${c.zone} ${c.citizenEmail || ""} ${c.assignedOfficerName || ""}`.toLowerCase().includes(query)) return false;
    if (filters.status && filters.status !== "ALL" && c.status !== filters.status) return false;
    if (filters.department && filters.department !== "ALL" && c.department !== filters.department) return false;
    if (filters.category && filters.category !== "ALL" && c.category !== filters.category) return false;
    if (filters.zone && filters.zone !== "ALL" && (c.zone || "Unspecified") !== filters.zone) return false;
    if (filters.ward && filters.ward !== "ALL" && (c.ward || "Unspecified") !== filters.ward) return false;
    if (filters.priority && filters.priority !== "ALL" && c.priority !== filters.priority) return false;
    if (filters.sla && filters.sla !== "ALL" && c.sla?.state !== filters.sla) return false;
    if (from && (!c.createdDate || c.createdDate < from)) return false;
    if (to && (!c.createdDate || c.createdDate > to)) return false;
    return true;
  });
}

export function distinctValues(complaints, field) {
  return [...new Set(complaints.map((c) => c[field] || "Unspecified"))].sort();
}

const CSV_COLUMNS = [
  ["Complaint ID", (c) => c.id],
  ["Title", (c) => c.title],
  ["Category", (c) => c.category],
  ["Department", (c) => c.department],
  ["Status", (c) => c.status],
  ["Priority", (c) => c.priority],
  ["Zone", (c) => c.zone],
  ["Ward", (c) => c.ward],
  ["Location", (c) => c.location],
  ["Latitude", (c) => c.latitude ?? ""],
  ["Longitude", (c) => c.longitude ?? ""],
  ["Assigned officer", (c) => c.assignedOfficerName || ""],
  ["Created at (UTC)", (c) => c.createdAt || ""],
  ["Resolved at (UTC)", (c) => c.resolvedAt || ""],
  ["Resolution hours", (c) => { const h = resolutionHours(c); return h === null ? "" : h.toFixed(1); }],
  ["SLA state", (c) => c.sla?.state || ""],
  ["Escalated", (c) => (c.escalated ? "yes" : "no")],
  ["Reopen count", (c) => c.reopenCount],
];

export function csvCell(value) {
  let text = value === null || value === undefined ? "" : String(value);
  // Neutralise spreadsheet formula injection.
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv(complaints) {
  const header = CSV_COLUMNS.map(([name]) => csvCell(name)).join(",");
  const rows = complaints.map((c) => CSV_COLUMNS.map(([, get]) => csvCell(get(c))).join(","));
  return [header, ...rows].join("\r\n");
}

export function downloadFile(filename, content, mime = "text/csv;charset=utf-8") {
  const blob = new Blob([mime.startsWith("text/csv") ? "﻿" : "", content], { type: mime });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function formatHours(hours) {
  if (hours === null || hours === undefined) return "—";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

export function formatPercent(rate) {
  return rate === null || rate === undefined ? "—" : `${Math.round(rate * 100)}%`;
}

export { DAY, HOUR };
