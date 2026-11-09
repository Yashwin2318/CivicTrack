import { describe, expect, it } from "vitest";
import { buildTimeline, canTransition, effectiveStatus, normalizeComplaint, parseDate, slaState } from "../lib/lifecycle";
import { applyFilters, csvCell, groupStats, resolutionHours, slaCompliance, summarize, toCsv, trend } from "../lib/analytics";
import { gridClusters, haversineKm, nearby, planVisitOrder, possibleDuplicates } from "../lib/geo";
import { buildComplaintPayload, validateComplaintForm } from "../lib/validation";
import { NOW, SAMPLE, raw } from "./fixtures";

const list = () => SAMPLE.map((c) => normalizeComplaint(c, NOW));

describe("lifecycle", () => {
  it("maps legacy PENDING + officer to ASSIGNED", () => {
    expect(effectiveStatus({ status: "PENDING", assignedOfficerSub: "o1" })).toBe("ASSIGNED");
    expect(effectiveStatus({ status: "PENDING" })).toBe("PENDING");
    expect(effectiveStatus({})).toBe("PENDING");
  });

  it("permits only valid transitions", () => {
    expect(canTransition("start", "ASSIGNED")).toBe(true);
    expect(canTransition("start", "PENDING")).toBe(false);
    expect(canTransition("resolve", "ASSIGNED")).toBe(false);
    expect(canTransition("reopen", "RESOLVED")).toBe(true);
    expect(canTransition("reopen", "CLOSED")).toBe(false);
  });

  it("parses naive timestamps as UTC", () => {
    expect(parseDate("2026-11-09T10:00:00").toISOString()).toBe("2026-11-09T10:00:00.000Z");
    expect(parseDate("garbage")).toBeNull();
  });

  it.each([[1, "on_track"], [44.5, "warning"], [48.1, "breached"]])("SLA state at %s h", (hours, state) => {
    expect(slaState(raw({ createdAt: new Date(NOW - hours * 3600000).toISOString() }), NOW).state).toBe(state);
  });

  it("evaluates met / missed for finished complaints", () => {
    const base = { status: "RESOLVED", createdAt: "2026-11-01T00:00:00Z" };
    expect(slaState({ ...base, resolvedAt: "2026-11-02T00:00:00Z" }, NOW).state).toBe("met");
    expect(slaState({ ...base, resolvedAt: "2026-11-04T00:00:00Z" }, NOW).state).toBe("missed");
  });

  it("normalises numeric strings and derives department", () => {
    const c = normalizeComplaint(raw({ latitude: "12.97", longitude: "77.59", category: "Streetlights" }), NOW);
    expect(c.hasCoords).toBe(true);
    expect(c.latitude).toBe(12.97);
    expect(c.department).toBe("Electrical");
    expect(normalizeComplaint(raw({ latitude: null }), NOW).hasCoords).toBe(false);
  });

  it("builds a timeline from legacy timestamps when history is absent", () => {
    const t = buildTimeline(normalizeComplaint(raw({ createdAt: "2026-11-09T01:00:00Z", assignedAt: "2026-11-09T05:00:00Z", resolvedAt: "2026-11-09T09:00:00Z" }), NOW));
    expect(t.map((x) => x.action)).toEqual(["created", "assigned", "resolved"]);
  });
});

describe("analytics", () => {
  it("summarises counts from records", () => {
    const s = summarize(list());
    expect(s).toMatchObject({ total: 5, pending: 1, assigned: 1, inProgress: 1, resolved: 1, closed: 1, overdue: 1, warning: 1, everReopened: 1 });
  });

  it("computes resolution time and SLA compliance", () => {
    const items = list();
    expect(resolutionHours(items[3])).toBeCloseTo(10);
    expect(slaCompliance(items)).toEqual({ met: 1, missed: 1, evaluated: 2, rate: 0.5 });
    expect(slaCompliance([]).rate).toBeNull();
  });

  it("groups by zone with Unspecified bucket", () => {
    const rows = groupStats(list(), (c) => c.zone);
    expect(rows.map((r) => [r.key, r.total])).toEqual([["East", 2], ["West", 2], ["Unspecified", 1]]);
  });

  it("gap-fills daily trends", () => {
    const series = trend(list(), "day");
    const created = series.reduce((a, b) => a + b.created, 0);
    expect(created).toBe(5);
    expect(series.length).toBeGreaterThanOrEqual(8);
  });

  it("filters by status, zone and date range", () => {
    expect(applyFilters(list(), { status: "PENDING" })).toHaveLength(1);
    expect(applyFilters(list(), { zone: "Unspecified" })).toHaveLength(1);
    expect(applyFilters(list(), { search: "aaaaaaaaa3" })).toHaveLength(1);
    expect(applyFilters(list(), { from: "2026-11-09", to: "2026-11-09" }).length).toBeGreaterThanOrEqual(1);
  });

  it("neutralises CSV formula injection and escapes quotes", () => {
    expect(csvCell("=HYPERLINK(1)")).toBe("'=HYPERLINK(1)");
    expect(csvCell('a "quoted", value')).toBe('"a ""quoted"", value"');
    const csv = toCsv(list());
    expect(csv.split("\r\n")).toHaveLength(6);
    expect(csv.startsWith("Complaint ID,")).toBe(true);
  });
});

describe("geo", () => {
  it("computes haversine distance", () => {
    // MG Road metro → Indiranagar metro ≈ 3.3 km
    expect(haversineKm({ latitude: 12.9755, longitude: 77.6068 }, { latitude: 12.9784, longitude: 77.6385 })).toBeCloseTo(3.45, 0);
  });

  it("finds nearby complaints and duplicates", () => {
    const items = list();
    const near = nearby(items[0], items, 0.5);
    expect(near.map((n) => n.complaint.id)).toEqual(["CT-AAAAAAAAA3"]);
    expect(possibleDuplicates(items[0], items)).toHaveLength(0); // different category
  });

  it("clusters and orders visits", () => {
    const items = list();
    expect(gridClusters(items, 0.01)).toHaveLength(1);
    const plan = planVisitOrder(null, items);
    expect(plan.order).toHaveLength(2);
    expect(plan.totalKm).toBeGreaterThan(0);
  });
});

describe("complaint form validation", () => {
  const valid = { title: "Broken streetlight", category: "Streetlights", location: "MG Road", description: "Light not working for a week", landmark: "", photo: null, coords: null };

  it("accepts a valid form", () => {
    expect(validateComplaintForm(valid)).toEqual({});
  });

  it("reports each invalid field", () => {
    const errors = validateComplaintForm({ ...valid, title: "abc", location: "", description: "short", category: "Bogus" });
    expect(Object.keys(errors).sort()).toEqual(["category", "description", "location", "title"]);
  });

  it("validates photo type and size", () => {
    expect(validateComplaintForm({ ...valid, photo: new File(["x"], "a.gif", { type: "image/gif" }) }).photo).toMatch(/JPG/);
    const big = new File([new Uint8Array(10 * 1024 * 1024 + 1)], "a.jpg", { type: "image/jpeg" });
    expect(validateComplaintForm({ ...valid, photo: big }).photo).toMatch(/10 MB/);
  });

  it("builds the API payload with rounded coordinates and idempotency key", () => {
    const body = buildComplaintPayload({ ...valid, title: "  Broken streetlight ", coords: { latitude: 12.97123456, longitude: 77.5912346, accuracy: 12.6 } }, "k.jpg", "req-123456789");
    expect(body).toEqual({ title: "Broken streetlight", category: "Streetlights", location: "MG Road", description: "Light not working for a week", latitude: 12.971235, longitude: 77.591235, locationAccuracy: 13, photoKey: "k.jpg", clientRequestId: "req-123456789" });
  });
});
