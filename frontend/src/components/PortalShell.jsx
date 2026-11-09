import { useState } from "react";
import { Bell, Building2, LogOut, Menu, RefreshCw, X } from "lucide-react";

/** Shared layout for all three portals: navy sidebar, topbar, mobile drawer. */
export default function PortalShell({ portal, nav, active, onNavigate, user, roleLabel, onLogout, onRefresh, refreshing, alerts = [], children, title }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [alertsOpen, setAlertsOpen] = useState(false);
  const initials = (user?.username || portal).replace(/[^a-zA-Z]/g, "").slice(0, 2).toUpperCase() || "CT";

  function go(id) {
    onNavigate(id);
    setMenuOpen(false);
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
              <button type="button" className="ct-icon-btn" aria-label={`Notifications (${alerts.length})`} aria-expanded={alertsOpen} onClick={() => setAlertsOpen((v) => !v)}>
                <Bell size={19} />{alerts.length > 0 && <span className="ct-dot">{alerts.length > 9 ? "9+" : alerts.length}</span>}
              </button>
              {alertsOpen && (
                <div className="ct-alerts-pop" role="dialog" aria-label="Notifications">
                  <strong>Notifications</strong>
                  {alerts.length === 0 && <p className="ct-muted">You're all caught up.</p>}
                  {alerts.slice(0, 8).map((a) => (
                    <button key={a.id} type="button" className={`ct-alert-item ct-alert-${a.tone || "info"}`} onClick={() => { setAlertsOpen(false); a.onClick?.(); }}>
                      <span>{a.title}</span><small>{a.detail}</small>
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
