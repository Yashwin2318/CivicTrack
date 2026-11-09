import { useMemo, useState } from "react";
import { AlertTriangle, ArrowUpRight, CheckCircle2, ClipboardList, Clock, ImagePlus, LayoutDashboard, MapPin, Plus, Search, UserCircle, X } from "lucide-react";
import PortalShell from "../components/PortalShell";
import ComplaintDetail from "../components/ComplaintDetail";
import { LocationPicker } from "../components/ComplaintMap";
import { useConfirm, useToast } from "../components/feedback";
import { ComplaintCards, EMPTY_FILTERS, FilterBar, useAuthContext, useComplaints } from "../components/complaints";
import { EmptyState, ErrorState, LoadingState, Panel, StatCard, formatDate } from "../components/ui";
import { api, newRequestId, uploadEvidence } from "../lib/api";
import { applyFilters, summarize } from "../lib/analytics";
import { getCurrentPosition } from "../lib/geo";
import { CATEGORIES, canTransition } from "../lib/lifecycle";
import { LIMITS, buildComplaintPayload, validateComplaintForm } from "../lib/validation";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "complaints", label: "My Complaints", icon: ClipboardList },
  { id: "track", label: "Track Complaint", icon: Search },
  { id: "profile", label: "My Profile", icon: UserCircle },
];

const EMPTY_FORM = { title: "", category: "Roads", location: "", landmark: "", description: "", photo: null, coords: null };

export function ComplaintForm({ auth, onCreated, onCancel }) {
  const toast = useToast();
  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [locating, setLocating] = useState(false);
  const [requestId] = useState(newRequestId);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  async function locate() {
    setLocating(true);
    try {
      set("coords", await getCurrentPosition());
    } catch (err) {
      toast(err.message, "error");
    } finally {
      setLocating(false);
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (submitting) return;
    const found = validateComplaintForm(form);
    setErrors(found);
    if (Object.keys(found).length) return;
    setSubmitting(true);
    try {
      const photoKey = form.photo ? await uploadEvidence({ auth, file: form.photo, uploadPath: "/evidence/upload-url" }) : undefined;
      const data = await api.createComplaint(auth, buildComplaintPayload(form, photoKey, requestId));
      if (!data.complaint?.complaintId) throw new Error("The server did not return a tracking ID.");
      onCreated(data.complaint);
    } catch (err) {
      toast(err.message || "Unable to submit complaint.", "error");
    } finally {
      setSubmitting(false);
    }
  }

  const field = (key) => ({ "aria-invalid": !!errors[key], "aria-describedby": errors[key] ? `err-${key}` : undefined });
  const err = (key) => errors[key] && <span className="ct-error" id={`err-${key}`}>{errors[key]}</span>;

  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal ct-modal-lg" onSubmit={submit} noValidate role="dialog" aria-modal="true" aria-labelledby="report-title">
        <div className="ct-modal-head">
          <div><p className="ct-eyebrow">CIVICTRACK SERVICES</p><h2 id="report-title">Report a civic issue</h2></div>
          <button type="button" className="ct-icon-btn" onClick={onCancel} aria-label="Close form"><X size={20} /></button>
        </div>
        <div className="ct-form-grid">
          <label className="ct-field ct-span-2">Issue title
            <input value={form.title} maxLength={LIMITS.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Large pothole near the bus stop" {...field("title")} />
            {err("title")}
          </label>
          <label className="ct-field">Category
            <select value={form.category} onChange={(e) => set("category", e.target.value)} {...field("category")}>
              {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
            </select>
            {err("category")}
          </label>
          <label className="ct-field">Nearest landmark <span className="ct-muted">(optional)</span>
            <input value={form.landmark} maxLength={LIMITS.landmark} onChange={(e) => set("landmark", e.target.value)} placeholder="e.g. Opposite City Hospital" {...field("landmark")} />
            {err("landmark")}
          </label>
          <label className="ct-field ct-span-2">Address / area
            <input value={form.location} maxLength={LIMITS.location} onChange={(e) => set("location", e.target.value)} placeholder="Street, area, ward" {...field("location")} />
            {err("location")}
          </label>
          <div className="ct-field ct-span-2">
            <span>Pin the exact spot <span className="ct-muted">(optional, helps officers find it)</span></span>
            <LocationPicker value={form.coords} onChange={(v) => set("coords", v)} onLocate={locate} locating={locating} />
            {err("coords")}
          </div>
          <label className="ct-field ct-span-2">Description
            <textarea rows={4} maxLength={LIMITS.description} value={form.description} onChange={(e) => set("description", e.target.value)} placeholder="What is the problem, how long has it existed, any safety risk?" {...field("description")} />
            <span className="ct-muted ct-small">{form.description.length}/{LIMITS.description}</span>
            {err("description")}
          </label>
          <label className="ct-field ct-span-2">Photo evidence <span className="ct-muted">(optional · JPG, PNG or WebP · max 10 MB)</span>
            <input type="file" accept="image/jpeg,image/png,image/webp" onChange={(e) => set("photo", e.target.files?.[0] || null)} {...field("photo")} />
            {form.photo && (
              <span className="ct-file-chip"><ImagePlus size={15} /> {form.photo.name} ({(form.photo.size / 1048576).toFixed(2)} MB)
                <button type="button" onClick={() => set("photo", null)} aria-label="Remove photo"><X size={14} /></button>
              </span>
            )}
            {err("photo")}
          </label>
        </div>
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={submitting}>
            {submitting ? (form.photo ? "Uploading evidence..." : "Submitting...") : <>Submit complaint <ArrowUpRight size={16} /></>}
          </button>
        </div>
      </form>
    </div>
  );
}

function TrackPanel({ complaints, onOpen }) {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);
  function search(e) {
    e.preventDefault();
    const id = query.trim().toUpperCase();
    setResult(complaints.find((c) => c.id === id) || "none");
  }
  return (
    <Panel title="Track a complaint" subtitle="Enter the tracking ID you received when you submitted (e.g. CT-1A2B3C4D5E).">
      <form className="ct-track" onSubmit={search}>
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="CT-XXXXXXXXXX" aria-label="Complaint tracking ID" />
        <button className="ct-btn ct-btn-primary" type="submit"><Search size={16} /> Track</button>
      </form>
      {result === "none" && <EmptyState title="No complaint found" message="Check the ID. You can only track complaints submitted from your account." />}
      {result && result !== "none" && <ComplaintCards complaints={[result]} onOpen={onOpen} />}
    </Panel>
  );
}

export default function CitizenPortal({ onLogout }) {
  const auth = useAuthContext();
  const toast = useToast();
  const confirm = useConfirm();
  const { complaints, loading, error, reload, replace } = useComplaints(auth);
  const [page, setPage] = useState("dashboard");
  const [showForm, setShowForm] = useState(false);
  const [selected, setSelected] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const stats = useMemo(() => summarize(complaints), [complaints]);
  const filtered = useMemo(() => applyFilters(complaints, filters), [complaints, filters]);

  const alerts = complaints
    .filter((c) => c.status === "RESOLVED")
    .map((c) => ({ id: c.id, title: `${c.id} was resolved`, detail: "Review the proof and confirm or reopen", tone: "success", onClick: () => setSelected(c) }));

  function created(raw) {
    const c = replace(raw);
    setShowForm(false);
    setPage("complaints");
    toast(`Complaint registered. Your tracking ID is ${c.id}.`);
  }

  async function reopen(c, update) {
    const reason = await confirm({ title: "Reopen this complaint?", message: "Tell the department what is still wrong. A new 48-hour SLA will start.", confirmLabel: "Reopen", requireText: true, textLabel: "Reason", textMinLength: 5 });
    if (!reason) return;
    try {
      const data = await api.reopen(auth, c.id, reason);
      update(replace(data.complaint));
      toast("Complaint reopened. The department has been notified.");
    } catch (err) { toast(err.message, "error"); }
  }

  async function close(c, update) {
    const ok = await confirm({ title: "Confirm the issue is fixed?", message: "Closing tells the department the resolution is satisfactory. This cannot be undone.", confirmLabel: "Yes, close it" });
    if (!ok) return;
    try {
      const data = await api.close(auth, c.id, {});
      update(replace(data.complaint));
      toast("Thank you! Complaint closed.");
    } catch (err) { toast(err.message, "error"); }
  }

  const actions = (c, update) => (canTransition("reopen", c.status) ? (
    <>
      <p className="ct-small">The officer marked this resolved. Is the issue fixed?</p>
      <button type="button" className="ct-btn ct-btn-primary" onClick={() => close(c, update)}><CheckCircle2 size={16} /> Yes, close it</button>
      <button type="button" className="ct-btn ct-btn-ghost" onClick={() => reopen(c, update)}><AlertTriangle size={16} /> No, reopen</button>
    </>
  ) : null);

  const reportButton = <button type="button" className="ct-btn ct-btn-primary" onClick={() => setShowForm(true)}><Plus size={17} /> Report an issue</button>;

  let body;
  if (loading) body = <LoadingState label="Loading your complaints..." />;
  else if (error) body = <ErrorState message={error} onRetry={reload} />;
  else if (page === "dashboard") {
    body = (
      <>
        <section className="ct-hero">
          <div><p className="ct-eyebrow">CIVIC SERVICES PLATFORM</p><h2>Good day{auth.user?.username ? `, ${auth.user.username.split("@")[0]}` : ""}</h2><p>Report civic issues, track progress and help build a better city.</p></div>
          {reportButton}
        </section>
        <div className="ct-stats">
          <StatCard label="Total complaints" value={stats.total} icon={ClipboardList} onClick={() => setPage("complaints")} />
          <StatCard label="Awaiting action" value={stats.pending + stats.assigned + stats.reopened} icon={Clock} tone="orange" />
          <StatCard label="In progress" value={stats.inProgress} icon={MapPin} tone="purple" />
          <StatCard label="Resolved / closed" value={stats.resolved + stats.closed} icon={CheckCircle2} tone="green" />
        </div>
        {stats.resolved > 0 && (
          <div className="ct-banner ct-banner-info" role="status"><CheckCircle2 size={18} /> {stats.resolved} complaint(s) were resolved. Please review the proof and confirm or reopen.</div>
        )}
        <Panel title="Recent complaints" subtitle="Your latest submissions" actions={<button type="button" className="ct-link" onClick={() => setPage("complaints")}>View all <ArrowUpRight size={14} /></button>}>
          {complaints.length ? <ComplaintCards complaints={complaints.slice(0, 4)} onOpen={setSelected} /> : <EmptyState title="No complaints yet" message="See a pothole, broken streetlight or garbage pile? Report it in under a minute." action={reportButton} />}
        </Panel>
      </>
    );
  } else if (page === "complaints") {
    body = (
      <Panel title="All my complaints" actions={reportButton}>
        <FilterBar filters={filters} onChange={setFilters} complaints={complaints} fields={["status", "category", "dates"]} resultCount={filtered.length} />
        {filtered.length ? <ComplaintCards complaints={filtered} onOpen={setSelected} /> : <EmptyState title="No matching complaints" message={complaints.length ? "Try clearing the filters." : "You haven't reported anything yet."} />}
      </Panel>
    );
  } else if (page === "track") {
    body = <TrackPanel complaints={complaints} onOpen={setSelected} />;
  } else {
    body = (
      <Panel title="My profile">
        <dl className="ct-meta ct-meta-wide">
          <div><dt>Email</dt><dd>{auth.user?.username || "—"}</dd></div>
          <div><dt>Account type</dt><dd>{auth.isDemo ? "Local demo citizen" : "Citizen (Cognito verified)"}</dd></div>
          <div><dt>Complaints submitted</dt><dd>{stats.total}</dd></div>
          <div><dt>Member activity</dt><dd>{complaints.length ? `First report ${formatDate(complaints[complaints.length - 1].createdDate)}` : "No reports yet"}</dd></div>
          <div><dt>Email updates</dt><dd>Sent to your account email at each stage. Delivery status is shown on each complaint.</dd></div>
        </dl>
        <button type="button" className="ct-btn ct-btn-ghost ct-mt" onClick={onLogout}>Sign out</button>
      </Panel>
    );
  }

  return (
    <PortalShell portal="Citizen Portal" nav={NAV} active={page} onNavigate={setPage} user={auth.user} roleLabel={auth.isDemo ? "Local demo" : "Resident"} onLogout={onLogout} onRefresh={reload} refreshing={loading} alerts={alerts}>
      {body}
      {showForm && <ComplaintForm auth={auth} onCreated={created} onCancel={() => setShowForm(false)} />}
      {selected && <ComplaintDetail key={selected.id} complaint={selected} auth={auth} role="Citizen" onClose={() => setSelected(null)} renderActions={actions} />}
    </PortalShell>
  );
}
