/* eslint-disable react-refresh/only-export-components */
import { useEffect, useMemo, useState } from "react";
import { Circle, CircleMarker, GeoJSON, MapContainer, Marker, Popup, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Crosshair, Search } from "lucide-react";
import { STATUS_LABELS } from "../lib/lifecycle";
import { centerOf, gridClusters, withCoords } from "../lib/geo";

export const STATUS_COLORS = {
  PENDING: "#d97706",
  ASSIGNED: "#7c3aed",
  IN_PROGRESS: "#2563eb",
  REOPENED: "#dc2626",
  RESOLVED: "#16a34a",
  CLOSED: "#64748b",
  REJECTED: "#7f1d1d",
};

// Category symbol (letter) and marker shape; colour always encodes status.
export const CATEGORY_STYLE = {
  Roads: { glyph: "R", shape: "square" },
  Sanitation: { glyph: "S", shape: "circle" },
  Streetlights: { glyph: "L", shape: "diamond" },
  Waterlogging: { glyph: "F", shape: "circle" },
  "Water Supply": { glyph: "W", shape: "square" },
  Other: { glyph: "O", shape: "circle" },
};

// Free OpenStreetMap tiles (attribution required). No paid map API is used.
const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

function categoryIcon(category, status, highlighted) {
  const style = CATEGORY_STYLE[category] || { glyph: (category || "?").charAt(0).toUpperCase(), shape: "circle" };
  const size = highlighted ? 28 : 22;
  return L.divIcon({
    className: "ct-pin-wrap",
    html: `<span class="ct-pin ct-pin-${style.shape}" style="background:${STATUS_COLORS[status] || "#475569"};width:${size}px;height:${size}px"><b>${style.glyph}</b></span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

function FitToPoints({ points }) {
  const map = useMap();
  const signature = points.map((p) => `${p.latitude},${p.longitude}`).join("|");
  useEffect(() => {
    if (!points.length) return;
    if (points.length === 1) map.setView([points[0].latitude, points[0].longitude], 15);
    else map.fitBounds(points.map((p) => [p.latitude, p.longitude]), { padding: [30, 30], maxZoom: 16 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, map]);
  return null;
}

function FlyTo({ target }) {
  const map = useMap();
  useEffect(() => {
    if (target) map.flyTo([target.latitude, target.longitude], target.zoom || 16);
  }, [target, map]);
  return null;
}

function ZoomWatcher({ onZoom }) {
  useMapEvents({ zoomend: (e) => onZoom(e.target.getZoom()) });
  return null;
}

/** Geocoding via OpenStreetMap Nominatim (free; usage policy: light, user-initiated
 * queries only, max 1 request/second, attribution). Only the typed text is sent. */
export async function searchPlace(query, signal) {
  const url = `https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&countrycodes=in&q=${encodeURIComponent(query)}`;
  const res = await fetch(url, { signal, headers: { "Accept-Language": "en" } });
  if (!res.ok) throw new Error(`Place search failed (${res.status}).`);
  const data = await res.json();
  return data.map((r) => ({ name: r.display_name, latitude: Number(r.lat), longitude: Number(r.lon) }));
}

export function PlaceSearch({ onPick, complaints = [], onSelectComplaint }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const local = query.trim().length >= 3
    ? complaints.filter((c) => `${c.location} ${c.landmark || ""} ${c.ward} ${c.zone}`.toLowerCase().includes(query.trim().toLowerCase())).slice(0, 5)
    : [];

  async function search(e) {
    e?.preventDefault();
    if (query.trim().length < 3) return;
    setBusy(true);
    setError("");
    try {
      setResults(await searchPlace(query.trim()));
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ct-place-search" role="search">
      <div className="ct-search">
        <Search size={16} aria-hidden="true" />
        <input value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") search(e); }}
          placeholder="Search a place or complaint address" aria-label="Search location" />
      </div>
      <button className="ct-btn ct-btn-ghost" type="button" onClick={search} disabled={busy}>{busy ? "Searching..." : "Find place"}</button>
      {(results.length > 0 || local.length > 0 || error) && (
        <ul className="ct-place-results">
          {local.map((c) => (
            <li key={c.id}><button type="button" className="ct-link" onClick={() => { onSelectComplaint?.(c); setResults([]); }}>{c.id}</button> {c.location}</li>
          ))}
          {results.map((r) => (
            <li key={`${r.latitude},${r.longitude}`}><button type="button" className="ct-link" onClick={() => { onPick({ ...r, zoom: 16 }); setResults([]); }}>Go to</button> {r.name}</li>
          ))}
          {error && <li className="ct-error">{error}</li>}
          {results.length > 0 && <li className="ct-muted ct-small">Search by OpenStreetMap Nominatim</li>}
        </ul>
      )}
    </div>
  );
}

function boundaryStyle(count, max) {
  const t = max ? count / max : 0;
  return { color: "#1e3a8a", weight: 1.5, fillColor: "#2563eb", fillOpacity: count ? 0.12 + 0.5 * t : 0.04 };
}

export default function ComplaintMap({ complaints, onSelect, height = 460, mode: initialMode = "markers", showModeSwitch = true, highlightId,
  boundaries = [], areaKey = "wardId", hotspots = [], showSearch = false, onMapClick }) {
  const [mode, setMode] = useState(initialMode);
  const [zoom, setZoom] = useState(12);
  const [showBoundaries, setShowBoundaries] = useState(true);
  const [flyTarget, setFlyTarget] = useState(null);
  const points = useMemo(() => withCoords(complaints), [complaints]);
  const center = centerOf(points);
  const cellDeg = zoom >= 15 ? 0.002 : zoom >= 13 ? 0.006 : zoom >= 11 ? 0.02 : 0.06;
  const clusters = useMemo(() => (mode === "markers" ? [] : gridClusters(points, mode === "density" ? 0.005 : cellDeg)), [points, mode, cellDeg]);
  const maxCount = Math.max(1, ...clusters.map((c) => c.count));
  const missing = complaints.length - points.length;
  const areaCounts = useMemo(() => {
    const counts = {};
    for (const c of complaints) if (c[areaKey]) counts[c[areaKey]] = (counts[c[areaKey]] || 0) + 1;
    return counts;
  }, [complaints, areaKey]);
  const withGeometry = boundaries.filter((b) => b.boundary);
  const maxArea = Math.max(0, ...withGeometry.map((b) => areaCounts[b.id] || 0));
  const usedCategories = [...new Set(points.map((c) => c.category))];

  return (
    <div className="ct-map-wrap">
      {(showModeSwitch || showSearch) && (
        <div className="ct-map-toolbar" role="group" aria-label="Map display options">
          {showModeSwitch && [["markers", "Markers"], ["clusters", "Clusters"], ["density", "Heatmap"]].map(([id, label]) => (
            <button key={id} type="button" className={`ct-chip ${mode === id ? "active" : ""}`} onClick={() => setMode(id)} aria-pressed={mode === id}>{label}</button>
          ))}
          {withGeometry.length > 0 && (
            <label className="ct-chip ct-chip-toggle"><input type="checkbox" checked={showBoundaries} onChange={(e) => setShowBoundaries(e.target.checked)} /> Boundaries</label>
          )}
          <span className="ct-muted ct-map-count">{points.length} mapped{missing > 0 ? ` · ${missing} without coordinates` : ""}</span>
        </div>
      )}
      {showSearch && <PlaceSearch onPick={setFlyTarget} complaints={complaints} onSelectComplaint={(c) => { if (c.hasCoords) setFlyTarget({ ...c, zoom: 17 }); onSelect?.(c); }} />}
      <div style={{ height }} className="ct-map">
        <MapContainer center={[center.latitude, center.longitude]} zoom={12} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
          <TileLayer url={TILE_URL} attribution={ATTRIBUTION} />
          <FitToPoints points={points} />
          <FlyTo target={flyTarget} />
          <ZoomWatcher onZoom={setZoom} />
          {onMapClick && <ClickToPick onPick={onMapClick} />}
          {showBoundaries && withGeometry.map((area) => (
            <GeoJSON key={`${area.id}-${areaCounts[area.id] || 0}`} data={{ type: "Feature", geometry: area.boundary, properties: {} }}
              style={() => boundaryStyle(areaCounts[area.id] || 0, maxArea)}>
              <Tooltip sticky>{area.name}{area.number ? ` (${area.number})` : ""}: {areaCounts[area.id] || 0} complaint(s)</Tooltip>
            </GeoJSON>
          ))}
          {mode === "markers" && points.map((c) => (
            <Marker key={c.id} position={[c.latitude, c.longitude]} icon={categoryIcon(c.category, c.status, c.id === highlightId)}
              eventHandlers={onSelect ? { click: () => onSelect(c) } : undefined}
              title={`${c.id} – ${c.category} – ${STATUS_LABELS[c.status]}`}>
              <Tooltip>{c.id} · {c.category} · {STATUS_LABELS[c.status]}<br />{c.title}</Tooltip>
            </Marker>
          ))}
          {mode === "clusters" && clusters.map((cl) => (
            <CircleMarker key={cl.key} center={[cl.latitude, cl.longitude]} radius={10 + Math.sqrt(cl.count) * 5}
              pathOptions={{ color: "#1e3a8a", weight: 2, fillColor: cl.open ? "#2563eb" : "#16a34a", fillOpacity: 0.75 }}>
              <Tooltip permanent direction="center" className="ct-cluster-label">{cl.count}</Tooltip>
              <Popup>
                <strong>{cl.count} complaint(s)</strong> · {cl.open} open
                <ul className="ct-popup-list">
                  {cl.items.slice(0, 8).map((c) => (
                    <li key={c.id}><button type="button" className="ct-link" onClick={() => onSelect?.(c)}>{c.id}</button> {c.title}</li>
                  ))}
                </ul>
              </Popup>
            </CircleMarker>
          ))}
          {mode === "density" && clusters.map((cl) => (
            <Circle key={cl.key} center={[cl.latitude, cl.longitude]} radius={250 + 150 * Math.sqrt(cl.count)}
              pathOptions={{ stroke: false, fillColor: "#dc2626", fillOpacity: 0.15 + 0.55 * (cl.count / maxCount) }}>
              <Tooltip>{cl.count} complaint(s) in this area</Tooltip>
            </Circle>
          ))}
          {hotspots.map((h) => (
            <CircleMarker key={`${h.latitude},${h.longitude}`} center={[h.latitude, h.longitude]} radius={14}
              pathOptions={{ color: "#b91c1c", weight: 3, dashArray: "4 4", fillOpacity: 0 }}>
              <Tooltip>Hotspot: {h.count} complaints · mostly {h.topCategory}</Tooltip>
            </CircleMarker>
          ))}
        </MapContainer>
      </div>
      <div className="ct-legend" aria-label="Map legend">
        {Object.entries(STATUS_COLORS).map(([status, color]) => (
          <span key={status}><i style={{ background: color }} />{STATUS_LABELS[status]}</span>
        ))}
        {mode === "markers" && usedCategories.map((cat) => (
          <span key={cat}><b className="ct-legend-glyph">{(CATEGORY_STYLE[cat] || { glyph: cat.charAt(0) }).glyph}</b>{cat}</span>
        ))}
      </div>
    </div>
  );
}

function ClickToPick({ onPick }) {
  useMapEvents({ click: (e) => onPick({ latitude: e.latlng.lat, longitude: e.latlng.lng, accuracy: null }) });
  return null;
}

/** Map + GPS button used on the complaint form. */
export function LocationPicker({ value, onChange, onLocate, locating, boundaries = [] }) {
  const center = value || { latitude: 12.9716, longitude: 77.5946 };
  const [flyTarget, setFlyTarget] = useState(null);
  return (
    <div className="ct-picker">
      <div className="ct-picker-bar">
        <button type="button" className="ct-btn ct-btn-ghost" onClick={onLocate} disabled={locating}>
          <Crosshair size={16} /> {locating ? "Locating..." : "Use my current location"}
        </button>
        <span className="ct-muted">{value ? `${value.latitude.toFixed(5)}, ${value.longitude.toFixed(5)}${value.accuracy ? ` (±${Math.round(value.accuracy)} m)` : ""}` : "or tap the map to drop a pin"}</span>
        {value && <button type="button" className="ct-link" onClick={() => onChange(null)}>Clear</button>}
      </div>
      <PlaceSearch onPick={(p) => { setFlyTarget(p); onChange({ latitude: p.latitude, longitude: p.longitude, accuracy: null }); }} />
      <div className="ct-map" style={{ height: 220 }}>
        <MapContainer center={[center.latitude, center.longitude]} zoom={value ? 16 : 11} style={{ height: "100%", width: "100%" }}>
          <TileLayer url={TILE_URL} attribution={ATTRIBUTION} />
          <ClickToPick onPick={onChange} />
          <FlyTo target={flyTarget} />
          {boundaries.filter((b) => b.boundary).map((b) => (
            <GeoJSON key={b.id} data={{ type: "Feature", geometry: b.boundary, properties: {} }} style={() => ({ color: "#1e3a8a", weight: 1, fillOpacity: 0.03 })}>
              <Tooltip sticky>{b.name}</Tooltip>
            </GeoJSON>
          ))}
          {value && !flyTarget && <FitToPoints points={[value]} />}
          {value && <CircleMarker center={[value.latitude, value.longitude]} radius={9} pathOptions={{ color: "#fff", weight: 2, fillColor: "#2563eb", fillOpacity: 1 }} />}
        </MapContainer>
      </div>
    </div>
  );
}

export function MiniMap({ latitude, longitude, height = 180 }) {
  return (
    <div className="ct-map" style={{ height }}>
      <MapContainer center={[latitude, longitude]} zoom={16} style={{ height: "100%", width: "100%" }} scrollWheelZoom={false}>
        <TileLayer url={TILE_URL} attribution={ATTRIBUTION} />
        <CircleMarker center={[latitude, longitude]} radius={9} pathOptions={{ color: "#fff", weight: 2, fillColor: "#dc2626", fillOpacity: 1 }} />
      </MapContainer>
    </div>
  );
}
