/* eslint-disable react-refresh/only-export-components */
import { useEffect, useMemo, useState } from "react";
import { Circle, CircleMarker, MapContainer, Popup, TileLayer, Tooltip, useMap, useMapEvents } from "react-leaflet";
import "leaflet/dist/leaflet.css";
import { Crosshair } from "lucide-react";
import { STATUS_LABELS } from "../lib/lifecycle";
import { centerOf, gridClusters, withCoords } from "../lib/geo";

export const STATUS_COLORS = {
  PENDING: "#d97706",
  ASSIGNED: "#7c3aed",
  IN_PROGRESS: "#2563eb",
  REOPENED: "#dc2626",
  RESOLVED: "#16a34a",
  CLOSED: "#64748b",
};

// Free OpenStreetMap tiles (attribution required). No paid map API is used.
const TILE_URL = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

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

function ZoomWatcher({ onZoom }) {
  useMapEvents({ zoomend: (e) => onZoom(e.target.getZoom()) });
  return null;
}

export default function ComplaintMap({ complaints, onSelect, height = 460, mode: initialMode = "markers", showModeSwitch = true, highlightId }) {
  const [mode, setMode] = useState(initialMode);
  const [zoom, setZoom] = useState(12);
  const points = useMemo(() => withCoords(complaints), [complaints]);
  const center = centerOf(points);
  // Cluster cell shrinks as the user zooms in.
  const cellDeg = zoom >= 15 ? 0.002 : zoom >= 13 ? 0.006 : zoom >= 11 ? 0.02 : 0.06;
  const clusters = useMemo(() => (mode === "markers" ? [] : gridClusters(points, mode === "density" ? 0.005 : cellDeg)), [points, mode, cellDeg]);
  const maxCount = Math.max(1, ...clusters.map((c) => c.count));
  const missing = complaints.length - points.length;

  return (
    <div className="ct-map-wrap">
      {showModeSwitch && (
        <div className="ct-map-toolbar" role="group" aria-label="Map display mode">
          {[["markers", "Markers"], ["clusters", "Clusters"], ["density", "Density"]].map(([id, label]) => (
            <button key={id} type="button" className={`ct-chip ${mode === id ? "active" : ""}`} onClick={() => setMode(id)} aria-pressed={mode === id}>{label}</button>
          ))}
          <span className="ct-muted ct-map-count">{points.length} mapped{missing > 0 ? ` · ${missing} without coordinates` : ""}</span>
        </div>
      )}
      <div style={{ height }} className="ct-map">
        <MapContainer center={[center.latitude, center.longitude]} zoom={12} scrollWheelZoom style={{ height: "100%", width: "100%" }}>
          <TileLayer url={TILE_URL} attribution={ATTRIBUTION} />
          <FitToPoints points={points} />
          <ZoomWatcher onZoom={setZoom} />
          {mode === "markers" && points.map((c) => (
            <CircleMarker key={c.id} center={[c.latitude, c.longitude]} radius={c.id === highlightId ? 11 : 8}
              pathOptions={{ color: "#fff", weight: 2, fillColor: STATUS_COLORS[c.status] || "#475569", fillOpacity: 0.95 }}
              eventHandlers={onSelect ? { click: () => onSelect(c) } : undefined}>
              <Tooltip>{c.id} · {STATUS_LABELS[c.status]}<br />{c.title}</Tooltip>
            </CircleMarker>
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
        </MapContainer>
      </div>
      <div className="ct-legend" aria-label="Status colours">
        {Object.entries(STATUS_COLORS).map(([status, color]) => (
          <span key={status}><i style={{ background: color }} />{STATUS_LABELS[status]}</span>
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
export function LocationPicker({ value, onChange, onLocate, locating }) {
  const center = value || { latitude: 12.9716, longitude: 77.5946 };
  return (
    <div className="ct-picker">
      <div className="ct-picker-bar">
        <button type="button" className="ct-btn ct-btn-ghost" onClick={onLocate} disabled={locating}>
          <Crosshair size={16} /> {locating ? "Locating..." : "Use my current location"}
        </button>
        <span className="ct-muted">{value ? `${value.latitude.toFixed(5)}, ${value.longitude.toFixed(5)}${value.accuracy ? ` (±${Math.round(value.accuracy)} m)` : ""}` : "or tap the map to drop a pin"}</span>
        {value && <button type="button" className="ct-link" onClick={() => onChange(null)}>Clear</button>}
      </div>
      <div className="ct-map" style={{ height: 220 }}>
        <MapContainer center={[center.latitude, center.longitude]} zoom={value ? 16 : 11} style={{ height: "100%", width: "100%" }}>
          <TileLayer url={TILE_URL} attribution={ATTRIBUTION} />
          <ClickToPick onPick={onChange} />
          {value && <FitToPoints points={[value]} />}
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
