import { useState } from "react";
import { Bell, Building2, LogOut, Menu, RefreshCw, X } from "lucide-react";
import { ACTION_LABELS } from "../lib/lifecycle";
import { formatDateTime } from "./ui";

/** Shared layout for all three portals: navy sidebar, topbar, notification centre, mobile drawer. */
export default function PortalShell({ portal, nav, active, onNavigate, user, roleLabel, onLogout, onRefresh, refreshing,
  alerts = [], feed = [], unread = 0, onOpenFeed, onSelectComplaint, children, title }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const initials = (user?.username || portal).replace(/[^a-zA-Z]/g, "").slice(0, 2).toUpperCase() || "CT";
  const badge = alerts.length + unread;

  function go(id) {
    onNavigate(id);
    setMenuOpen(false);
  }

  function toggleAlerts() {
    setAlertsOpen((open) => {
      if (!open) onOpenFeed?.();
      return !open;
    });
  }

  return (
    <div className="ct-shell">
      <a href="#ct-main" className="ct-skip">Skip to content</a>
      <aside className={`ct-sidebar ${menuOpen ? "open" : ""}`} aria-label="Primary">
        <div className="ct-brand">
          <span className="ct-brand-icon"><Building2 size={22} /></span>
          <div>
            <strong>CivicTrack</strong>
            <small>{portal}</small>
          </div>
          <button type="button" className="ct-icon-btn ct-only-mobile" onClick={() => setMenuOpen(false)} aria-label="Close menu"><X size={20} /></button>
        </div>
        <nav className="ct-nav">
          {nav.map(({ id, label, icon: Icon, count }) => (
            <button key={id} type="button" className={`ct-nav-item ${active === id ? "active" : ""}`} onClick={() => go(id)} aria-current={active === id ? "page" : undefined}>
              <Icon size={18} /><span>{label}</span>
              {count > 0 && <span className="ct-nav-count">{count}</span>}
            </button>
          ))}
        </nav>
        <div className="ct-profile">
          <span className="ct-avatar">{initials}</span>
          <div className="ct-profile-info">
            <strong title={user?.username}>{user?.username || "Signed in"}</strong>
            <span>{roleLabel}</span>
          </div>
          <button type="button" className="ct-icon-btn" onClick={onLogout} title="Sign out" aria-label="Sign out"><LogOut size={17} /></button>
        </div>
      </aside>
      {menuOpen && <button type="button" className="ct-scrim" onClick={() => setMenuOpen(false)} aria-label="Close navigation" />}

      <main className="ct-main" id="ct-main">
        <header className="ct-topbar">
          <div className="ct-topbar-left">
            <button type="button" className="ct-icon-btn ct-only-mobile" onClick={() => setMenuOpen(true)} aria-label="Open menu"><Menu size={21} /></button>
            <div>
              <div className="ct-crumb">{portal} / {nav.find((n) => n.id === active)?.label}</div>
              <h1>{title || nav.find((n) => n.id === active)?.label}</h1>
            </div>
          </div>
          <div className="ct-topbar-right">
            {onRefresh && (
              <button type="button" className="ct-icon-btn" onClick={onRefresh} aria-label="Refresh data" disabled={refreshing}>
                <RefreshCw size={18} className={refreshing ? "ct-spin" : ""} />
              </button>
            )}
            <div className="ct-alerts">
              <button type="button" className="ct-icon-btn" aria-label={`Notifications (${badge})`} aria-expanded={alertsOpen} onClick={toggleAlerts}>
                <Bell size={19} />{badge > 0 && <span className="ct-dot">{badge > 9 ? "9+" : badge}</span>}
              </button>
              {alertsOpen && (
                <div className="ct-alerts-pop" role="dialog" aria-label="Notifications">
                  <div className="ct-alerts-head"><strong>Notifications</strong>
                    <button type="button" className="ct-icon-btn" onClick={() => setAlertsOpen(false)} aria-label="Close notifications"><X size={16} /></button>
                  </div>
                  {alerts.length > 0 && <small className="ct-alerts-section">Needs attention</small>}
                  {alerts.slice(0, 6).map((a) => (
                    <button key={a.id} type="button" className={`ct-alert-item ct-alert-${a.tone || "info"}`} onClick={() => { setAlertsOpen(false); a.onClick?.(); }}>
                      <span>{a.title}</span><small>{a.detail}</small>
                    </button>
                  ))}
                  <small className="ct-alerts-section">Recent activity (30 days)</small>
                  {feed.length === 0 && <p className="ct-muted ct-small">No recent activity.</p>}
                  {feed.slice(0, 10).map((n) => (
                    <button key={`${n.complaintId}-${n.at}-${n.action}`} type="button" className="ct-alert-item" onClick={() => { setAlertsOpen(false); onSelectComplaint?.(n.complaintId); }}>
                      <span>{ACTION_LABELS[n.action] || n.action}: {n.complaintId}</span>
                      <small>{n.title}{n.officerName ? ` · ${n.officerName}` : ""} · {formatDateTime(n.at)}</small>
                      {n.note && <small className="ct-alert-note">“{n.note}”</small>}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </header>
        <div className="ct-content">{children}</div>
      </main>
    </div>
  );
}
