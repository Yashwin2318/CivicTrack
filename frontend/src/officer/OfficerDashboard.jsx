import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, Camera, CheckCircle2, ClipboardList, Clock, History, LayoutDashboard, ListChecks, Map as MapIcon, Navigation, PlayCircle, Route, ThumbsDown, ThumbsUp, UserCircle, X } from "lucide-react";
import PortalShell from "../components/PortalShell";
import ComplaintDetail from "../components/ComplaintDetail";
import ComplaintMap from "../components/ComplaintMap";
import { useConfirm, useToast } from "../components/feedback";
import { ComplaintCards, ComplaintTable, EMPTY_FILTERS, FilterBar, useAuthContext, useComplaints } from "../components/complaints";
import { useNotifications, useReference } from "../components/data";
import { EmptyState, ErrorState, LoadingState, Panel, SlaBadge, StatCard, formatDateTime, useNow } from "../components/ui";
import { api, validateImageFile } from "../lib/api";
import { applyFilters, average, formatHours, resolutionHours, slaCompliance, formatPercent } from "../lib/analytics";
import { directionsUrl, getCurrentPosition, planVisitOrder, withCoords } from "../lib/geo";
import { FINAL_STATUSES, OPEN_STATUSES, canTransition, slaState } from "../lib/lifecycle";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "assigned", label: "Assigned Work", icon: ClipboardList },
  { id: "map", label: "Field Map", icon: MapIcon },
  { id: "sla", label: "SLA Monitor", icon: Clock },
  { id: "history", label: "Work History", icon: History },
  { id: "profile", label: "My Profile", icon: UserCircle },
];

const MODE_TEXT = {
  start: { title: "Start work", photo: "Before photo (optional)", note: "Note (optional)", placeholder: "e.g. Reached site, crew dispatched", submit: "Start work" },
  progress: { title: "Add progress update", photo: "Progress photo (optional)", note: "Progress note *", placeholder: "What has been done so far?", submit: "Save update" },
  resolve: { title: "Resolve complaint", photo: "After photo (resolution proof) *", note: "Resolution note (optional)", placeholder: "What was done?", submit: "Upload proof & resolve" },
};

export function WorkModal({ complaint, mode, onSubmit, onCancel }) {
  const [file, setFile] = useState(null);
  const [note, setNote] = useState("");
  const [percent, setPercent] = useState(complaint.percentComplete ?? 50);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const text = MODE_TEXT[mode];

  async function submit(e) {
    e.preventDefault();
    if (mode === "resolve" && !file) { setError("A resolution proof photograph is required."); return; }
    if (mode === "progress" && note.trim().length < 3) { setError("Describe the progress made."); return; }
    if (file) {
      const problem = validateImageFile(file);
      if (problem) { setError(problem); return; }
    }
    setBusy(true);
    setError("");
    try {
      await onSubmit({ file, note: note.trim(), percent: Number(percent) });
    } catch (err) {
      setError(err.message || "Request failed.");
      setBusy(false);
    }
  }

  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal" onSubmit={submit} noValidate role="dialog" aria-modal="true" aria-labelledby="work-title">
        <div className="ct-modal-head">
          <div><h3 id="work-title">{text.title}</h3><p className="ct-muted">{complaint.id} · {complaint.title}</p></div>
          <button type="button" className="ct-icon-btn" onClick={onCancel} aria-label="Close"><X size={19} /></button>
        </div>
        <label className="ct-field">{text.photo}
          <input autoFocus type="file" accept="image/jpeg,image/png,image/webp" capture="environment" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          <small className="ct-muted">JPG, PNG or WebP · max 10 MB</small>
        </label>
        {mode === "progress" && (
          <label className="ct-field">Estimated completion: {percent}%
            <input type="range" min={0} max={95} step={5} value={percent} onChange={(e) => setPercent(e.target.value)} />
          </label>
        )}
        <label className="ct-field">{text.note}
          <textarea rows={3} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} placeholder={text.placeholder} />
        </label>
        {error && <p className="ct-error" role="alert">{error}</p>}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={busy}>{busy ? "Saving..." : text.submit}</button>
        </div>
      </form>
    </div>
  );
}

function RoutePlanner({ complaints, onOpen }) {
  const toast = useToast();
  const [start, setStart] = useState(null);
  const [locating, setLocating] = useState(false);
  const open = useMemo(() => withCoords(complaints.filter((c) => OPEN_STATUSES.has(c.status))), [complaints]);
  const plan = useMemo(() => planVisitOrder(start, open), [start, open]);

  async function locate() {
    setLocating(true);
    try { setStart(await getCurrentPosition()); } catch (err) { toast(err.message, "error"); } finally { setLocating(false); }
  }

  return (
    <Panel title="Suggested visit order" subtitle="Nearest-next ordering using straight-line distance. A planning aid only — your position is used once in this browser and never sent to the server; this is not road routing or live tracking."
      actions={<button type="button" className="ct-btn ct-btn-ghost" onClick={locate} disabled={locating}><Route size={15} /> {locating ? "Locating..." : start ? "Update my position" : "Start from my position"}</button>}>
      {!open.length ? <EmptyState title="No mapped open complaints" message="Complaints submitted with a map pin appear here." /> : (
        <>
          <ol className="ct-route">
            {plan.order.map(({ complaint: c, legKm }, i) => (
              <li key={c.id}>
                <span className="ct-route-n">{i + 1}</span>
                <div><button type="button" className="ct-link" onClick={() => onOpen(c)}>{c.id}</button> {c.title}
                  <div className="ct-muted ct-small">{legKm ? `${legKm.toFixed(2)} km from previous` : start ? "from your position" : "start"} · <SlaBadge sla={c.sla} /></div>
                </div>
                <a className="ct-icon-btn" href={directionsUrl(c.latitude, c.longitude)} target="_blank" rel="noreferrer noopener" aria-label={`Directions to ${c.id}`}><Navigation size={16} /></a>
              </li>
            ))}
          </ol>
          <p className="ct-muted ct-small">Approximate straight-line total: {plan.totalKm.toFixed(1)} km{start ? " including the first leg from your position" : ""}.</p>
        </>
      )}
    </Panel>
  );
}

function sameDay(date, now) {
  return date && new Date(date).toDateString() === new Date(now).toDateString();
}

function OfficerProfile({ auth, active, finished, now }) {
  const [me, setMe] = useState(null);
  useEffect(() => {
    let cancelled = false;
    api.me(auth).then((d) => { if (!cancelled) setMe(d.user); }).catch(() => {});
    return () => { cancelled = true; };
  }, [auth]);
  const p = me?.profile;
  return (
    <Panel title="Officer profile">
      <dl className="ct-meta ct-meta-wide">
        <div><dt>Account</dt><dd>{auth.user?.username || "—"}</dd></div>
        <div><dt>Role</dt><dd>Field Officer{auth.isDemo ? " (local demo)" : ""}</dd></div>
        <div><dt>Department</dt><dd>{p?.departmentName || "Not assigned"}</dd></div>
        <div><dt>Zones</dt><dd>{p?.zoneNames?.length ? p.zoneNames.join(", ") : "—"}</dd></div>
        <div><dt>Wards</dt><dd>{p?.wardNames?.length ? p.wardNames.join(", ") : "—"}</dd></div>
        <div><dt>Availability</dt><dd>{p ? (p.available ? "Available for assignments" : "Marked unavailable") : "—"}</dd></div>
        <div><dt>Workload</dt><dd>{active.length} active{p ? ` of ${p.maxActive} maximum` : ""}</dd></div>
        <div><dt>Completed</dt><dd>{finished.length}</dd></div>
        <div><dt>Last refreshed</dt><dd>{formatDateTime(new Date(now))}</dd></div>
      </dl>
      <p className="ct-muted ct-small">Department, zones, wards and availability are set by an administrator. You can only view and update complaints assigned to you.</p>
    </Panel>
  );
}

export default function OfficerDashboard({ onLogout }) {
  const auth = useAuthContext();
  const toast = useToast();
  const confirm = useConfirm();
  const now = useNow(60000);
  const { complaints: loaded, loading, error, reload, replace, loadedAt } = useComplaints(auth);
  const { ref } = useReference(auth, { boundaries: true });
  const { feed, unread, markSeen } = useNotifications(auth, loadedAt);
  const [page, setPage] = useState("dashboard");
  const [selected, setSelected] = useState(null);
  const [work, setWork] = useState(null); // {complaint, mode, update}
  const [filters, setFilters] = useState(EMPTY_FILTERS);

  // Recompute SLA every minute so countdowns stay live.
  const complaints = useMemo(() => loaded.map((c) => ({ ...c, sla: slaState(c, now) })), [loaded, now]);
  const active = useMemo(() => complaints.filter((c) => OPEN_STATUSES.has(c.status)), [complaints]);
  const finished = useMemo(() => complaints.filter((c) => FINAL_STATUSES.has(c.status) && c.status !== "REJECTED"), [complaints]);
  const overdue = active.filter((c) => c.sla.state === "breached");
  const dueSoon = active.filter((c) => c.sla.state === "warning");
  const awaitingResponse = active.filter((c) => (c.status === "ASSIGNED" || c.status === "REOPENED") && !c.acceptedAt);
  const filtered = useMemo(() => applyFilters(active, filters), [active, filters]);
  const bySla = [...active].sort((a, b) => (a.sla.remainingMs ?? Infinity) - (b.sla.remainingMs ?? Infinity));
  const today = {
    assigned: complaints.filter((c) => sameDay(c.assignedAt, now)).length,
    dueIn24h: active.filter((c) => c.sla.remainingMs !== null && c.sla.remainingMs <= 24 * 3600000).length,
    resolved: complaints.filter((c) => sameDay(c.resolvedAt, now)).length,
  };

  const alerts = [
    ...overdue.map((c) => ({ id: `o-${c.id}`, title: `${c.id} is overdue`, detail: c.title, tone: "danger", onClick: () => setSelected(c) })),
    ...dueSoon.map((c) => ({ id: `w-${c.id}`, title: `${c.id} due soon`, detail: c.title, tone: "warning", onClick: () => setSelected(c) })),
    ...awaitingResponse.map((c) => ({ id: `n-${c.id}`, title: `${c.status === "REOPENED" ? "Reopened" : "New"}: ${c.id} — accept or decline`, detail: c.title, onClick: () => setSelected(c) })),
  ];

  async function respond(c, update, accept) {
    try {
      let data;
      if (accept) {
        data = await api.accept(auth, c.id);
        toast(`Accepted ${c.id}.`);
      } else {
        const reason = await confirm({ title: `Decline ${c.id}?`, message: "The complaint returns to the administrator for reassignment.", confirmLabel: "Decline", tone: "danger", requireText: true, textLabel: "Reason (shown to the administrator)", textMinLength: 10 });
        if (!reason) return;
        data = await api.decline(auth, c.id, reason);
        toast(`Declined ${c.id}. The administrator has been notified.`, "info");
        setSelected(null);
      }
      update?.(replace(data.complaint));
      if (!accept) reload({ quiet: true });
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function submitWork({ file, note, percent }) {
    const { complaint: c, mode, update } = work;
    let data;
    if (mode === "start") {
      const beforePhotoKey = file ? await api.uploadOfficerPhoto(auth, c.id, file, "before") : undefined;
      data = await api.start(auth, c.id, { ...(note ? { note } : {}), ...(beforePhotoKey ? { beforePhotoKey } : {}) });
      toast(`Work started on ${c.id}.`);
    } else if (mode === "progress") {
      const photoKey = file ? await api.uploadOfficerPhoto(auth, c.id, file, "progress") : undefined;
      data = await api.progress(auth, c.id, { note, percentComplete: percent, ...(photoKey ? { photoKey } : {}) });
      toast(`Progress recorded for ${c.id}.`);
    } else {
      const proofPhotoKey = await api.uploadOfficerPhoto(auth, c.id, file, "after");
      data = await api.resolve(auth, c.id, { proofPhotoKey, ...(note ? { note } : {}) });
      toast(`${c.id} resolved. The citizen has been notified.`);
    }
    const updated = replace(data.complaint);
    update?.(updated);
    setWork(null);
  }

  const actionButtons = (c, update) => (
    <>
      {canTransition("accept", c.status) && !c.acceptedAt && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => respond(c, update, true)}><ThumbsUp size={15} /> Accept</button>}
      {canTransition("decline", c.status) && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => respond(c, update, false)}><ThumbsDown size={15} /> Decline</button>}
      {canTransition("start", c.status) && <button type="button" className="ct-btn ct-btn-primary" onClick={() => setWork({ complaint: c, mode: "start", update })}><PlayCircle size={15} /> Start work</button>}
      {canTransition("progress", c.status) && <button type="button" className="ct-btn ct-btn-ghost" onClick={() => setWork({ complaint: c, mode: "progress", update })}><ListChecks size={15} /> Update progress</button>}
      {canTransition("resolve", c.status) && <button type="button" className="ct-btn ct-btn-primary" onClick={() => setWork({ complaint: c, mode: "resolve", update })}><Camera size={15} /> Resolve with proof</button>}
      {c.hasCoords && <a className="ct-btn ct-btn-ghost" href={directionsUrl(c.latitude, c.longitude)} target="_blank" rel="noreferrer noopener"><Navigation size={15} /> Directions</a>}
    </>
  );

  let body;
  if (loading && !loaded.length) body = <LoadingState label="Loading your assigned complaints..." />;
  else if (error) body = <ErrorState message={error} onRetry={reload} />;
  else if (page === "dashboard") {
    body = (
      <>
        {overdue.length > 0 && <div className="ct-banner ct-banner-danger" role="alert"><AlertTriangle size={18} /> {overdue.length} assigned complaint(s) have breached the SLA. Prioritise these now.</div>}
        <div className="ct-stats">
          <StatCard label="Active assignments" value={active.length} icon={ClipboardList} onClick={() => setPage("assigned")} />
          <StatCard label="Awaiting your response" value={awaitingResponse.length} icon={Clock} tone="orange" />
          <StatCard label="In progress" value={active.filter((c) => c.status === "IN_PROGRESS").length} icon={PlayCircle} tone="purple" />
          <StatCard label="Overdue" value={overdue.length} icon={AlertTriangle} tone="red" onClick={() => setPage("sla")} />
          <StatCard label="Resolved by me" value={finished.length} icon={CheckCircle2} tone="green" onClick={() => setPage("history")} />
        </div>
        <Panel title="Today's workload">
          <div className="ct-stats ct-stats-compact">
            <StatCard label="Assigned today" value={today.assigned} />
            <StatCard label="Due within 24 h" value={today.dueIn24h} tone="orange" />
            <StatCard label="Resolved today" value={today.resolved} tone="green" />
          </div>
        </Panel>
        <Panel title="Most urgent" subtitle="Ordered by time left on the SLA">
          {bySla.length ? <ComplaintCards complaints={bySla.slice(0, 5)} onOpen={setSelected} renderAction={(c) => actionButtons(c)} /> : <EmptyState title="No active assignments" message="New assignments from the administrator will appear here." />}
        </Panel>
      </>
    );
  } else if (page === "assigned") {
    body = (
      <Panel title="Assigned work">
        <FilterBar filters={filters} onChange={setFilters} complaints={active} fields={["status", "category", "priority", "sla"]} resultCount={filtered.length} />
        {filtered.length ? <ComplaintCards complaints={filtered} onOpen={setSelected} renderAction={(c) => actionButtons(c)} /> : <EmptyState title="Nothing matches" message={active.length ? "Clear the filters to see all work." : "You have no active assignments."} />}
      </Panel>
    );
  } else if (page === "map") {
    body = (
      <>
        <Panel title="Field map" subtitle="Your open assignments with a map pin. Marker letter = category, colour = status.">
          <ComplaintMap complaints={active} onSelect={setSelected} height={420} showSearch boundaries={ref.wards} />
        </Panel>
        <RoutePlanner complaints={active} onOpen={setSelected} />
      </>
    );
  } else if (page === "sla") {
    body = (
      <Panel title="SLA monitor" subtitle={`${ref.settings.slaHours}-hour resolution target · warnings ${ref.settings.warningHours} hours before the deadline · ${overdue.length} overdue, ${dueSoon.length} due soon`}>
        {bySla.length ? <ComplaintTable complaints={bySla} onOpen={setSelected} columns={["id", "title", "status", "priority", "sla", "date"]} /> : <EmptyState title="No active SLAs" />}
      </Panel>
    );
  } else if (page === "history") {
    const compliance = slaCompliance(finished);
    const rated = finished.filter((c) => c.citizenRating);
    body = (
      <>
        <div className="ct-stats">
          <StatCard label="Completed" value={finished.length} icon={CheckCircle2} tone="green" />
          <StatCard label="Avg. resolution time" value={formatHours(average(finished.map(resolutionHours)))} icon={Clock} />
          <StatCard label="SLA compliance" value={formatPercent(compliance.rate)} note={`${compliance.met} of ${compliance.evaluated} within SLA`} icon={CheckCircle2} tone="purple" />
          <StatCard label="Citizen rating" value={rated.length ? `${average(rated.map((c) => c.citizenRating)).toFixed(1)} / 5` : "—"} note={`${rated.length} rating(s)`} icon={ThumbsUp} tone="orange" />
        </div>
        <Panel title="Work history">
          {finished.length ? <ComplaintTable complaints={finished} onOpen={setSelected} columns={["id", "title", "status", "sla", "date"]} /> : <EmptyState title="No completed work yet" />}
        </Panel>
      </>
    );
  } else {
    body = <OfficerProfile auth={auth} active={active} finished={finished} now={now} />;
  }

  return (
    <PortalShell portal="Officer Portal" nav={NAV.map((n) => (n.id === "sla" ? { ...n, count: overdue.length } : n))} active={page} onNavigate={setPage}
      user={auth.user} roleLabel="Field Officer" onLogout={onLogout} onRefresh={reload} refreshing={loading} alerts={alerts}
      feed={feed} unread={unread} onOpenFeed={markSeen} onSelectComplaint={(id) => setSelected(complaints.find((c) => c.id === id) || null)}>
      {body}
      {selected && <ComplaintDetail key={selected.id} complaint={selected} auth={auth} role="Officer" allComplaints={complaints} onOpenOther={setSelected} onClose={() => setSelected(null)} renderActions={actionButtons} />}
      {work && <WorkModal complaint={work.complaint} mode={work.mode} onSubmit={submitWork} onCancel={() => setWork(null)} />}
    </PortalShell>
  );
}
