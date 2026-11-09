// Geographic helpers. Distances are straight-line (haversine) estimates —
// no road network or live vehicle data is used or implied.
import { OPEN_STATUSES } from "./lifecycle";

const EARTH_RADIUS_KM = 6371;
const toRad = (deg) => (deg * Math.PI) / 180;

export function haversineKm(a, b) {
  const dLat = toRad(b.latitude - a.latitude);
  const dLng = toRad(b.longitude - a.longitude);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.latitude)) * Math.cos(toRad(b.latitude)) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(h)));
}

export const withCoords = (complaints) => complaints.filter((c) => c.hasCoords);

/** Other complaints within radiusKm, nearest first. */
export function nearby(target, complaints, radiusKm = 0.5) {
  if (!target?.hasCoords) return [];
  return withCoords(complaints)
    .filter((c) => c.id !== target.id)
    .map((c) => ({ complaint: c, distanceKm: haversineKm(target, c) }))
    .filter((x) => x.distanceKm <= radiusKm)
    .sort((a, b) => a.distanceKm - b.distanceKm);
}

/** Open complaints of the same category within radius — likely duplicates. */
export function possibleDuplicates(target, complaints, radiusKm = 0.2) {
  return nearby(target, complaints, radiusKm).filter(({ complaint }) => complaint.category === target.category && OPEN_STATUSES.has(complaint.status));
}

/** Grid clustering. cellDeg ≈ 0.01° ≈ 1.1 km. */
export function gridClusters(complaints, cellDeg = 0.01) {
  const cells = new Map();
  for (const c of withCoords(complaints)) {
    const key = `${Math.floor(c.latitude / cellDeg)}:${Math.floor(c.longitude / cellDeg)}`;
    if (!cells.has(key)) cells.set(key, []);
    cells.get(key).push(c);
  }
  return [...cells.entries()].map(([key, items]) => ({
    key,
    count: items.length,
    latitude: items.reduce((s, c) => s + c.latitude, 0) / items.length,
    longitude: items.reduce((s, c) => s + c.longitude, 0) / items.length,
    open: items.filter((c) => OPEN_STATUSES.has(c.status)).length,
    items,
  }));
}

export function centerOf(complaints, fallback = { latitude: 12.9716, longitude: 77.5946 }) {
  const points = withCoords(complaints);
  if (!points.length) return fallback;
  return {
    latitude: points.reduce((s, c) => s + c.latitude, 0) / points.length,
    longitude: points.reduce((s, c) => s + c.longitude, 0) / points.length,
  };
}

/** Greedy nearest-neighbour visiting order from a start point. A planning aid
 * only: straight-line distances, not road routing. */
export function planVisitOrder(start, complaints) {
  const remaining = withCoords(complaints).slice();
  const order = [];
  let current = start;
  let totalKm = 0;
  while (remaining.length) {
    let bestIndex = 0;
    let bestDistance = Infinity;
    remaining.forEach((c, i) => {
      const d = current ? haversineKm(current, c) : 0;
      if (d < bestDistance) { bestDistance = d; bestIndex = i; }
    });
    const [next] = remaining.splice(bestIndex, 1);
    totalKm += current ? bestDistance : 0;
    order.push({ complaint: next, legKm: current ? bestDistance : 0 });
    current = next;
  }
  return { order, totalKm };
}

export function directionsUrl(latitude, longitude) {
  return `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${latitude},${longitude}`)}`;
}

export function searchUrl(text) {
  return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(text)}`;
}

export function getCurrentPosition(options = { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 }) {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Location services are not available in this browser."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy: pos.coords.accuracy }),
      (err) => reject(new Error(err.code === 1 ? "Location permission was denied." : "Unable to determine your location.")),
      options
    );
  });
}
