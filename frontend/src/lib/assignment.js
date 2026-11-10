import { haversineKm, withCoords } from "./geo";

/** Transparent ranking for assignment suggestions. */
export function rankOfficers(complaint, officers, departments = []) {
  // Legacy complaints store only the department name; resolve it to an ID.
  const departmentId = complaint.departmentId || departments.find((d) => d.name === complaint.department)?.id;
  return officers.filter((o) => o.enabled !== false).map((o) => {
    const p = o.profile || {};
    const reasons = [];
    let score = 0;
    if (p.departmentId && departmentId && p.departmentId === departmentId) { score += 40; reasons.push("same department"); }
    if (complaint.wardId && p.wardIds?.includes(complaint.wardId)) { score += 30; reasons.push("covers this ward"); }
    else if (complaint.zoneId && p.zoneIds?.includes(complaint.zoneId)) { score += 20; reasons.push("covers this zone"); }
    const near = complaint.hasCoords ? withCoords(o.activeComplaints).filter((c) => haversineKm(complaint, c) <= 2).length : 0;
    if (near) { score += Math.min(15, near * 5); reasons.push(`${near} job(s) within 2 km`); }
    const capacity = (p.maxActive || 15) - o.active;
    score += Math.max(-30, Math.min(15, capacity));
    if (capacity <= 0) reasons.push("at workload limit");
    if (o.overdue) { score -= 5 * o.overdue; reasons.push(`${o.overdue} overdue`); }
    if (p.available === false) { score -= 100; reasons.push("marked unavailable"); }
    return { ...o, score, reasons, unavailable: p.available === false };
  }).sort((a, b) => b.score - a.score || a.active - b.active);
}
