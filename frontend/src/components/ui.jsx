/* eslint-disable react-refresh/only-export-components */
import { useEffect, useState } from "react";
import { AlertTriangle, Inbox, Loader2, RefreshCw } from "lucide-react";
import { STATUS_LABELS } from "../lib/lifecycle";

export function StatusBadge({ status }) {
  return <span className={`ct-badge ct-status-${(status || "PENDING").toLowerCase()}`}>{STATUS_LABELS[status] || status}</span>;
}

export function PriorityBadge({ priority }) {
  const p = priority || "MEDIUM";
  return <span className={`ct-badge ct-priority-${p.toLowerCase()}`}>{p.charAt(0) + p.slice(1).toLowerCase()}</span>;
}

export function formatRemaining(ms) {
  const abs = Math.abs(ms);
  const h = Math.floor(abs / 3600000);
  const m = Math.floor((abs % 3600000) / 60000);
  const text = h >= 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h}h ${m}m`;
  return ms < 0 ? `${text} overdue` : `${text} left`;
}

const SLA_TEXT = { on_track: "On track", warning: "Due soon", breached: "Overdue", met: "SLA met", missed: "SLA missed", unknown: "No SLA data" };

export function SlaBadge({ sla, showTime = true }) {
  if (!sla) return null;
  const time = showTime && sla.remainingMs !== null && ["on_track", "warning", "breached"].includes(sla.state) ? ` · ${formatRemaining(sla.remainingMs)}` : "";
  return <span className={`ct-badge ct-sla-${sla.state}`} title={sla.deadline ? `Deadline ${formatDateTime(sla.deadline)}` : undefined}>{SLA_TEXT[sla.state]}{time}</span>;
}

export function StatCard({ label, value, note, icon: Icon, tone = "blue", onClick }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag className={`ct-stat ${onClick ? "ct-stat-clickable" : ""}`} onClick={onClick} type={onClick ? "button" : undefined}>
      {Icon && <span className={`ct-stat-icon ct-tone-${tone}`}><Icon size={20} /></span>}
      <span className="ct-stat-value">{value}</span>
      <span className="ct-stat-label">{label}</span>
      {note && <span className="ct-stat-note">{note}</span>}
    </Tag>
  );
}

export function LoadingState({ label = "Loading..." }) {
  return <div className="ct-state" role="status"><Loader2 className="ct-spin" size={22} /><span>{label}</span></div>;
}

export function EmptyState({ title = "Nothing here yet", message, action }) {
  return (
    <div className="ct-state">
      <Inbox size={26} />
      <strong>{title}</strong>
      {message && <span>{message}</span>}
      {action}
    </div>
  );
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="ct-state ct-state-error" role="alert">
      <AlertTriangle size={24} />
      <strong>Something went wrong</strong>
      <span>{message}</span>
      {onRetry && <button type="button" className="ct-btn ct-btn-ghost" onClick={onRetry}><RefreshCw size={15} /> Retry</button>}
    </div>
  );
}

export function Panel({ title, subtitle, actions, children, className = "" }) {
  return (
    <section className={`ct-panel ${className}`}>
      {(title || actions) && (
        <header className="ct-panel-head">
          <div>{title && <h3>{title}</h3>}{subtitle && <p>{subtitle}</p>}</div>
          {actions && <div className="ct-panel-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function formatDate(value) {
  if (!value) return "—";
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
}

export function formatDateTime(value) {
  if (!value) return "—";
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Re-render periodically so SLA countdowns stay current. */
export function useNow(intervalMs = 60000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs);
    return () => clearInterval(id);
  }, [intervalMs]);
  return now;
}

export function Select({ label, value, onChange, options, allLabel = "All" }) {
  return (
    <label className="ct-select">
      <span>{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {allLabel && <option value="ALL">{allLabel}</option>}
        {options.map((o) => (typeof o === "string" ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>))}
      </select>
    </label>
  );
}
