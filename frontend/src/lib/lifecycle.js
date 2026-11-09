// Mirrors backend/complaints/lifecycle.py. The backend is authoritative;
// these helpers only drive display and which buttons are offered.

export const STATUS = Object.freeze({
  PENDING: "PENDING",
  ASSIGNED: "ASSIGNED",
  IN_PROGRESS: "IN_PROGRESS",
  RESOLVED: "RESOLVED",
  REOPENED: "REOPENED",
  CLOSED: "CLOSED",
});

export const STATUS_ORDER = ["PENDING", "ASSIGNED", "IN_PROGRESS", "REOPENED", "RESOLVED", "CLOSED"];

export const STATUS_LABELS = {
  PENDING: "Pending",
  ASSIGNED: "Assigned",
  IN_PROGRESS: "In Progress",
  RESOLVED: "Resolved",
  REOPENED: "Reopened",
  CLOSED: "Closed",
};

export const OPEN_STATUSES = new Set(["PENDING", "ASSIGNED", "IN_PROGRESS", "REOPENED"]);
export const FINAL_STATUSES = new Set(["RESOLVED", "CLOSED"]);

export const TRANSITIONS = {
  assign: ["PENDING", "REOPENED"],
  reassign: ["ASSIGNED"],
  unassign: ["ASSIGNED"],
  start: ["ASSIGNED", "REOPENED"],
  resolve: ["IN_PROGRESS"],
  reopen: ["RESOLVED"],
  close: ["RESOLVED"],
};

export const CATEGORIES = ["Roads", "Sanitation", "Streetlights", "Waterlogging", "Water Supply", "Other"];

export const CATEGORY_DEPARTMENTS = {
  Roads: "Public Works",
  Sanitation: "Solid Waste Management",
  Streetlights: "Electrical",
  Waterlogging: "Storm Water Drains",
  "Water Supply": "Water Supply",
  Other: "General Administration",
};

export const DEPARTMENTS = [...new Set(Object.values(CATEGORY_DEPARTMENTS))].sort();
export const PRIORITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
export const DEFAULT_SLA_HOURS = 48;
export const SLA_WARNING_HOURS = 4;
const HOUR = 3600000;

export function effectiveStatus(item) {
  const status = item?.status || "PENDING";
  if (status === "PENDING" && item?.assignedOfficerSub) return "ASSIGNED";
  return status;
}

export function canTransition(action, status) {
  return (TRANSITIONS[action] || []).includes(status);
}

export function departmentFor(item) {
  const stored = typeof item?.department === "string" ? item.department.trim() : "";
  return stored || CATEGORY_DEPARTMENTS[item?.category] || "General Administration";
}

export function normalizePriority(value) {
  const upper = typeof value === "string" ? value.trim().toUpperCase() : "";
  return PRIORITIES.includes(upper) ? upper : "MEDIUM";
}

export function parseDate(value) {
  if (!value || typeof value !== "string") return null;
  // Naive ISO timestamps are UTC (matches backend).
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(value.trim());
  const date = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function slaHoursOf(item) {
  const hours = parseInt(item?.slaHours ?? DEFAULT_SLA_HOURS, 10);
  return Number.isFinite(hours) ? Math.max(1, hours) : DEFAULT_SLA_HOURS;
}

/** state: unknown | on_track | warning | breached | met | missed */
export function slaState(item, now = Date.now(), warningHours = SLA_WARNING_HOURS) {
  const start = parseDate(item?.slaStartedAt) || parseDate(item?.createdAt);
  if (!start) return { state: "unknown", deadline: null, remainingMs: null };
  const hours = slaHoursOf(item);
  const deadline = new Date(start.getTime() + hours * HOUR);
  const warningAt = deadline.getTime() - Math.min(warningHours, hours) * HOUR;
  const status = effectiveStatus(item);
  const remainingMs = deadline.getTime() - now;

  let state;
  if (FINAL_STATUSES.has(status)) {
    const finished = parseDate(item?.resolvedAt) || parseDate(item?.closedAt);
    state = !finished || finished <= deadline ? "met" : "missed";
  } else if (now >= deadline.getTime()) {
    state = "breached";
  } else if (now >= warningAt) {
    state = "warning";
  } else {
    state = "on_track";
  }
  return { state, deadline, remainingMs, hours };
}

function toNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** Normalise an API complaint into the shape every portal uses. Works with
 * both the current backend and older responses that lack derived fields. */
export function normalizeComplaint(raw, now = Date.now()) {
  const status = raw.effectiveStatus || effectiveStatus(raw);
  const latitude = toNumber(raw.latitude);
  const longitude = toNumber(raw.longitude);
  return {
    ...raw,
    id: raw.complaintId,
    storedStatus: raw.status,
    status,
    statusLabel: STATUS_LABELS[status] || status,
    department: departmentFor(raw),
    priority: normalizePriority(raw.priority),
    title: raw.title || "Untitled complaint",
    category: raw.category || "Other",
    location: raw.location || "Location not provided",
    zone: raw.zone || "",
    ward: raw.ward || "",
    createdDate: parseDate(raw.createdAt),
    resolvedDate: parseDate(raw.resolvedAt),
    latitude,
    longitude,
    hasCoords: latitude !== null && longitude !== null,
    sla: slaState(raw, now),
    reopenCount: Number(raw.reopenCount || 0),
    history: Array.isArray(raw.history) ? raw.history : [],
  };
}

/** Timeline from audit history, falling back to legacy timestamps. */
export function buildTimeline(complaint) {
  if (complaint.history?.length) {
    return complaint.history.map((entry) => ({ ...entry, date: parseDate(entry.at) }));
  }
  const legacy = [
    ["created", complaint.createdAt],
    ["assigned", complaint.assignedAt],
    ["work_started", complaint.workStartedAt],
    ["sla_escalated", complaint.escalatedAt],
    ["resolved", complaint.resolvedAt],
  ];
  return legacy
    .filter(([, at]) => at)
    .map(([action, at]) => ({ action, at, date: parseDate(at), officerName: action === "assigned" ? complaint.assignedOfficerName : undefined }))
    .sort((a, b) => (a.date?.getTime() || 0) - (b.date?.getTime() || 0));
}

export const ACTION_LABELS = {
  created: "Complaint submitted",
  assigned: "Assigned to officer",
  reassigned: "Reassigned",
  unassigned: "Officer unassigned",
  work_started: "Work started",
  resolved: "Resolved with proof",
  reopened: "Reopened by citizen",
  closed: "Closed",
  details_updated: "Details updated",
  sla_escalated: "SLA breached – escalated",
};
