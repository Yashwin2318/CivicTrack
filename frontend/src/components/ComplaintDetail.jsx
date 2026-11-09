import { useEffect, useState } from "react";
import { Building2, Clock, ExternalLink, ImageOff, Mail, MapPin, Navigation, User, X } from "lucide-react";
import { api } from "../lib/api";
import { ACTION_LABELS, STATUS_LABELS, buildTimeline, normalizeComplaint } from "../lib/lifecycle";
import { directionsUrl, nearby, searchUrl } from "../lib/geo";
import { MiniMap } from "./ComplaintMap";
import { PriorityBadge, SlaBadge, StatusBadge, formatDateTime } from "./ui";

function Photo({ url, label, hasKey }) {
  const [failed, setFailed] = useState(false);
  if (!hasKey) return null;
  return (
    <figure className="ct-photo">
      {url && !failed ? (
        <a href={url} target="_blank" rel="noreferrer noopener" title="Open full size (link expires in 5 minutes)">
          <img src={url} alt={label} loading="lazy" onError={() => setFailed(true)} />
        </a>
      ) : (
        <div className="ct-photo-missing"><ImageOff size={22} /><span>{failed ? "Preview link expired — refresh" : "Preview available after backend update"}</span></div>
      )}
      <figcaption>{label}</figcaption>
    </figure>
  );
}

export function Timeline({ complaint }) {
  const items = buildTimeline(complaint);
  if (!items.length) return <p className="ct-muted">No history recorded.</p>;
  return (
    <ol className="ct-timeline">
      {items.map((h, i) => (
        <li key={`${h.at}-${i}`} className={`ct-tl-${h.action}`}>
          <span className="ct-tl-dot" aria-hidden="true" />
          <div>
            <strong>{ACTION_LABELS[h.action] || h.action}</strong>
            {h.toStatus && h.fromStatus !== h.toStatus && <span className="ct-muted"> → {STATUS_LABELS[h.toStatus] || h.toStatus}</span>}
            <div className="ct-muted ct-small">{formatDateTime(h.date || h.at)}{h.actorRole ? ` · ${h.actorRole}` : ""}</div>
            {h.officerName && <div className="ct-small">Officer: {h.officerName}{h.previousOfficerName ? ` (was ${h.previousOfficerName})` : ""}</div>}
            {!h.officerName && h.previousOfficerName && <div className="ct-small">Previous officer: {h.previousOfficerName}</div>}
            {h.note && <blockquote>{h.note}</blockquote>}
          </div>
        </li>
      ))}
    </ol>
  );
}

const NOTIFICATION_TEXT = { created: "registration", assigned: "assignment", started: "work started", resolved: "resolution" };

export default function ComplaintDetail({ complaint: initial, auth, onClose, renderActions, allComplaints = [], role, onOpenOther }) {
  const [complaint, setComplaint] = useState(initial);

  // Refresh for fresh presigned URLs; fall back to list data on older backends.
  useEffect(() => {
    let cancelled = false;
    api.getComplaint(auth, initial.id)
      .then((data) => { if (!cancelled && data.complaint) setComplaint(normalizeComplaint(data.complaint)); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [auth, initial.id]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const c = complaint;
  const near = role !== "Citizen" ? nearby(c, allComplaints, 0.5).slice(0, 5) : [];
  const notification = c.lastCitizenNotification;

  return (
    <div className="ct-drawer-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()} role="presentation">
      <aside className="ct-drawer" role="dialog" aria-modal="true" aria-labelledby="ct-detail-title">
        <header className="ct-drawer-head">
          <div>
            <span className="ct-muted ct-small">{c.id}</span>
            <h2 id="ct-detail-title">{c.title}</h2>
            <div className="ct-badges"><StatusBadge status={c.status} /><PriorityBadge priority={c.priority} /><SlaBadge sla={c.sla} /></div>
          </div>
          <button type="button" className="ct-icon-btn" onClick={onClose} aria-label="Close details"><X size={20} /></button>
        </header>

        {renderActions && <div className="ct-drawer-actions">{renderActions(c, setComplaint)}</div>}

        <section className="ct-drawer-section">
          <dl className="ct-meta">
            <div><dt><Building2 size={14} /> Department</dt><dd>{c.department} · {c.category}</dd></div>
            <div><dt><MapPin size={14} /> Location</dt><dd>{c.location}{c.landmark ? ` (near ${c.landmark})` : ""}</dd></div>
            {(c.zone || c.ward) && <div><dt>Zone / Ward</dt><dd>{c.zone || "—"} / {c.ward || "—"}</dd></div>}
            <div><dt><Clock size={14} /> Submitted</dt><dd>{formatDateTime(c.createdDate)}</dd></div>
            {c.sla?.deadline && <div><dt>SLA deadline</dt><dd>{formatDateTime(c.sla.deadline)} ({c.sla.hours} h)</dd></div>}
            <div><dt><User size={14} /> Officer</dt><dd>{c.assignedOfficerName || "Not yet assigned"}</dd></div>
            {role !== "Citizen" && c.citizenEmail && <div><dt><Mail size={14} /> Reported by</dt><dd>{c.citizenEmail}</dd></div>}
            {c.resolvedDate && <div><dt>Resolved</dt><dd>{formatDateTime(c.resolvedDate)}</dd></div>}
            {c.reopenCount > 0 && <div><dt>Reopened</dt><dd>{c.reopenCount} time(s)</dd></div>}
            {c.citizenRating && <div><dt>Citizen rating</dt><dd>{c.citizenRating} / 5</dd></div>}
            {notification && (
              <div><dt>Email update</dt><dd>{notification.sent ? "Sent" : "Not delivered"} for {NOTIFICATION_TEXT[notification.kind] || notification.kind} · {formatDateTime(notification.at)}</dd></div>
            )}
          </dl>
          <p className="ct-description">{c.description}</p>
          {c.resolutionNote && <p className="ct-description"><strong>Resolution note:</strong> {c.resolutionNote}</p>}
        </section>

        <section className="ct-drawer-section">
          <h4>Location</h4>
          {c.hasCoords ? (
            <>
              <MiniMap latitude={c.latitude} longitude={c.longitude} />
              <a className="ct-btn ct-btn-ghost ct-mt" href={directionsUrl(c.latitude, c.longitude)} target="_blank" rel="noreferrer noopener"><Navigation size={15} /> Get directions <ExternalLink size={13} /></a>
            </>
          ) : (
            <a className="ct-btn ct-btn-ghost" href={searchUrl(c.location)} target="_blank" rel="noreferrer noopener"><MapPin size={15} /> Search this address on a map <ExternalLink size={13} /></a>
          )}
        </section>

        {(c.photoKey || c.beforePhotoKey || c.proofPhotoKey) && (
          <section className="ct-drawer-section">
            <h4>Evidence</h4>
            <div className="ct-photos">
              <Photo hasKey={!!c.photoKey} url={c.photoUrl} label="Citizen evidence" />
              <Photo hasKey={!!c.beforePhotoKey} url={c.beforePhotoUrl} label="Officer – before work" />
              <Photo hasKey={!!c.proofPhotoKey} url={c.proofPhotoUrl} label="Resolution proof" />
            </div>
          </section>
        )}

        {near.length > 0 && (
          <section className="ct-drawer-section">
            <h4>Nearby complaints (within 500 m)</h4>
            <ul className="ct-near">
              {near.map(({ complaint: n, distanceKm }) => (
                <li key={n.id}>
                  <button type="button" className="ct-link" onClick={() => onOpenOther?.(n)}>{n.id}</button> {n.title}
                  <span className="ct-muted"> · {Math.round(distanceKm * 1000)} m · {STATUS_LABELS[n.status]}{n.category === c.category ? " · same category" : ""}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section className="ct-drawer-section">
          <h4>Status timeline</h4>
          <Timeline complaint={c} />
        </section>
      </aside>
    </div>
  );
}
