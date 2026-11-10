import { useMemo, useState } from "react";
import { AlertTriangle, ArrowUpRight, CheckCircle2, ClipboardList, Clock, ImagePlus, LayoutDashboard, Map as MapIcon, MapPin, Plus, Search, Sparkles, Star, UserCircle, X } from "lucide-react";
import { updatePassword, updateUserAttributes } from "aws-amplify/auth";
import PortalShell from "../components/PortalShell";
import ComplaintDetail from "../components/ComplaintDetail";
import ComplaintMap, { LocationPicker } from "../components/ComplaintMap";
import { useConfirm, useToast } from "../components/feedback";
import { ComplaintCards, EMPTY_FILTERS, FilterBar, useAuthContext, useComplaints } from "../components/complaints";
import { useNotifications, useReference } from "../components/data";
import { EmptyState, ErrorState, LoadingState, Panel, StatCard, formatDate } from "../components/ui";
import { api, newRequestId, uploadEvidence } from "../lib/api";
import { applyFilters, summarize } from "../lib/analytics";
import { getCurrentPosition } from "../lib/geo";
import { canTransition } from "../lib/lifecycle";
import { LIMITS, buildComplaintPayload, passwordProblem, validateComplaintForm } from "../lib/validation";

const NAV = [
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard },
  { id: "complaints", label: "My Complaints", icon: ClipboardList },
  { id: "map", label: "Map", icon: MapIcon },
  { id: "track", label: "Track Complaint", icon: Search },
  { id: "profile", label: "My Profile", icon: UserCircle },
];

const EMPTY_FORM = { title: "", category: "Roads", location: "", landmark: "", description: "", photo: null, coords: null, urgency: "MEDIUM" };

export function ComplaintForm({ auth, reference, initialCoords = null, onCreated, onCancel }) {
  const toast = useToast();
  const categories = reference.categories.filter((c) => c.active !== false);
  const categoryNames = categories.map((c) => c.name);
  const [form, setForm] = useState({ ...EMPTY_FORM, coords: initialCoords, category: categoryNames.includes("Roads") ? "Roads" : categoryNames[0] || "Other" });
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);
  const [locating, setLocating] = useState(false);
  const [review, setReview] = useState(null); // result of /complaints/similar, shown before final submit
  const [requestId] = useState(newRequestId);
  const set = (key, value) => { setForm((f) => ({ ...f, [key]: value })); setReview(null); };
  const department = reference.departments.find((d) => d.id === categories.find((c) => c.name === form.category)?.departmentId);

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

  async function checkFirst() {
    try {
      const body = { title: form.title.trim(), description: form.description.trim(), category: form.category, citizenUrgency: form.urgency,
        ...(form.coords ? { latitude: Number(form.coords.latitude.toFixed(6)), longitude: Number(form.coords.longitude.toFixed(6)) } : {}) };
      return await api.similar(auth, body);
    } catch {
      return { duplicates: [], suggestedCategory: {}, unavailable: true };
    }
  }

  async function submit(event) {
    event.preventDefault();
    if (submitting) return;
    const found = validateComplaintForm(form, categoryNames);
    setErrors(found);
    if (Object.keys(found).length) return;
    setSubmitting(true);
    try {
      // Step 1: duplicate / category check (shown once; the citizen can then submit anyway).
      if (!review) {
        const result = await checkFirst();
        const suggestion = result.suggestedCategory?.category;
        if (result.duplicates?.length || (suggestion && suggestion !== form.category && categoryNames.includes(suggestion))) {
          setReview(result);
          setSubmitting(false);
          return;
        }
      }
      // Step 2: upload evidence and create the complaint.
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
  const suggestion = review?.suggestedCategory?.category;

  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal ct-modal-lg" onSubmit={submit} noValidate role="dialog" aria-modal="true" aria-labelledby="report-title">
        <div className="ct-modal-head">
          <div><p className="ct-eyebrow">CIVICTRACK SERVICES</p><h2 id="report-title">Report a civic issue</h2></div>
          <button type="button" className="ct-icon-btn" onClick={onCancel} aria-label="Close form"><X size={20} /></button>
        </div>
        <div className="ct-form-grid">
          <label className="ct-field ct-span-2">Issue title
            <input autoFocus value={form.title} maxLength={LIMITS.title} onChange={(e) => set("title", e.target.value)} placeholder="e.g. Large pothole near the bus stop" {...field("title")} />
            {err("title")}
          </label>
          <label className="ct-field">Category
            <select value={form.category} onChange={(e) => set("category", e.target.value)} {...field("category")}>
              {categoryNames.map((c) => <option key={c}>{c}</option>)}
            </select>
            {department && <small className="ct-muted">Routed to: {department.name}</small>}
            {err("category")}
          </label>
          <label className="ct-field">How urgent is it?
            <select value={form.urgency} onChange={(e) => set("urgency", e.target.value)}>
              <option value="LOW">Low – inconvenience</option>
              <option value="MEDIUM">Medium – needs attention</option>
              <option value="HIGH">High – safety risk</option>
            </select>
          </label>
          <label className="ct-field ct-span-2">Address / area
            <input value={form.location} maxLength={LIMITS.location} onChange={(e) => set("location", e.target.value)} placeholder="Street, area, ward" {...field("location")} />
            {err("location")}
          </label>
          <label className="ct-field ct-span-2">Nearest landmark <span className="ct-muted">(optional)</span>
            <input value={form.landmark} maxLength={LIMITS.landmark} onChange={(e) => set("landmark", e.target.value)} placeholder="e.g. Opposite City Hospital" {...field("landmark")} />
            {err("landmark")}
          </label>
          <div className="ct-field ct-span-2">
            <span>Pin the exact spot <span className="ct-muted">(optional, helps officers find it and detects your ward)</span></span>
            <LocationPicker value={form.coords} onChange={(v) => set("coords", v)} onLocate={locate} locating={locating} boundaries={reference.wards} />
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
        {review && (
          <div className="ct-review" role="status">
            {review.duplicates?.length > 0 && (
              <p><AlertTriangle size={16} /> <strong>{review.duplicates.length} open complaint(s) about {form.category.toLowerCase()} already exist within 200 m</strong>
                {review.duplicates.some((d) => d.yours) ? " — including one of yours." : "."} The department may already be working on it.
              </p>
            )}
            {review.duplicates?.filter((d) => d.yours).map((d) => <p key={d.complaintId} className="ct-small">Your complaint {d.complaintId}: {d.title} ({d.distanceMeters} m away)</p>)}
            {suggestion && suggestion !== form.category && categoryNames.includes(suggestion) && (
              <p><Sparkles size={16} /> Your description sounds like <strong>{suggestion}</strong>.{" "}
                <button type="button" className="ct-link" onClick={() => { set("category", suggestion); }}>Use {suggestion}</button>
              </p>
            )}
            <p className="ct-small ct-muted">Suggestions are rule-based (keywords and distance). Submit again to continue.</p>
          </div>
        )}
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={submitting}>
            {submitting ? (form.photo ? "Uploading evidence..." : "Checking...") : review ? <>Submit anyway <ArrowUpRight size={16} /></> : <>Submit complaint <ArrowUpRight size={16} /></>}
          </button>
        </div>
      </form>
    </div>
  );
}

function CloseDialog({ complaint, onSubmit, onCancel }) {
  const [rating, setRating] = useState(0);
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <div className="ct-modal-backdrop" role="presentation">
      <form className="ct-modal ct-modal-sm" role="dialog" aria-modal="true" aria-labelledby="close-title"
        onSubmit={async (e) => { e.preventDefault(); setBusy(true); await onSubmit({ ...(rating ? { rating } : {}), ...(feedback.trim() ? { feedback: feedback.trim() } : {}) }); setBusy(false); }}>
        <h3 id="close-title">Confirm the issue is fixed</h3>
        <p className="ct-muted">{complaint.id} · {complaint.title}. Closing cannot be undone.</p>
        <fieldset className="ct-stars">
          <legend>How satisfied are you? (optional)</legend>
          {[1, 2, 3, 4, 5].map((n) => (
            <label key={n} title={`${n} star${n > 1 ? "s" : ""}`}>
              <input type="radio" name="rating" value={n} checked={rating === n} onChange={() => setRating(n)} className="ct-sr" />
              <Star size={26} fill={n <= rating ? "#f59e0b" : "none"} color={n <= rating ? "#f59e0b" : "#94a3b8"} aria-hidden="true" />
              <span className="ct-sr">{n} stars</span>
            </label>
          ))}
        </fieldset>
        <label className="ct-field">Feedback (optional)<textarea rows={3} maxLength={1000} value={feedback} onChange={(e) => setFeedback(e.target.value)} /></label>
        <div className="ct-modal-actions">
          <button type="button" className="ct-btn ct-btn-ghost" onClick={onCancel}>Cancel</button>
          <button type="submit" className="ct-btn ct-btn-primary" disabled={busy}><CheckCircle2 size={16} /> Close complaint</button>
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

function ProfilePanel({ auth, stats, complaints, onLogout }) {
  const toast = useToast();
  const [name, setName] = useState(auth.session?.tokens?.idToken?.payload?.name || "");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState("");
  const demo = auth.isDemo;

  async function saveName(e) {
    e.preventDefault();
    setBusy("name");
    try {
      await updateUserAttributes({ userAttributes: { name: name.trim() } });
      toast("Name updated. It appears on emails after your next sign-in.");
    } catch (err) { toast(err.message, "error"); } finally { setBusy(""); }
  }

  async function savePassword(e) {
    e.preventDefault();
    const problem = passwordProblem(newPassword);
    if (problem) { toast(problem, "error"); return; }
    setBusy("password");
    try {
      await updatePassword({ oldPassword, newPassword });
      setOldPassword(""); setNewPassword("");
      toast("Password changed.");
    } catch (err) { toast(err.message, "error"); } finally { setBusy(""); }
  }

  return (
    <>
      <Panel title="My profile">
        <dl className="ct-meta ct-meta-wide">
          <div><dt>Email</dt><dd>{auth.user?.username || "—"}</dd></div>
          <div><dt>Account type</dt><dd>{demo ? "Local demo citizen" : "Citizen (Cognito verified)"}</dd></div>
          <div><dt>Complaints submitted</dt><dd>{stats.total}</dd></div>
          <div><dt>Member activity</dt><dd>{complaints.length ? `First report ${formatDate(complaints[complaints.length - 1].createdDate)}` : "No reports yet"}</dd></div>
          <div><dt>Email updates</dt><dd>Sent to your account email at each stage. Delivery status is shown on each complaint.</dd></div>
        </dl>
      </Panel>
      <div className="ct-grid-2">
        <Panel title="Display name">
          <form onSubmit={saveName} className="ct-stack">
            <label className="ct-field">Full name<input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} disabled={demo} required /></label>
            <button type="submit" className="ct-btn ct-btn-primary" disabled={demo || busy === "name" || !name.trim()}>Save name</button>
          </form>
        </Panel>
        <Panel title="Change password">
          <form onSubmit={savePassword} className="ct-stack">
            <label className="ct-field">Current password<input type="password" autoComplete="current-password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} disabled={demo} required /></label>
            <label className="ct-field">New password<input type="password" autoComplete="new-password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} disabled={demo} required /></label>
            <button type="submit" className="ct-btn ct-btn-primary" disabled={demo || busy === "password"}>Change password</button>
          </form>
        </Panel>
      </div>
      {demo && <p className="ct-muted ct-small">Profile changes are disabled for local demo accounts.</p>}
      <button type="button" className="ct-btn ct-btn-ghost" onClick={onLogout}>Sign out</button>
    </>
  );
}

export default function CitizenPortal({ onLogout }) {
  const auth = useAuthContext();
  const toast = useToast();
  const confirm = useConfirm();
  const { complaints, loading, error, reload, replace, loadedAt } = useComplaints(auth);
  const { ref } = useReference(auth, { boundaries: true });
  const { feed, unread, markSeen } = useNotifications(auth, loadedAt);
  const [page, setPage] = useState("dashboard");
  const [formCoords, setFormCoords] = useState(undefined); // undefined = form closed
  const [selected, setSelected] = useState(null);
  const [closing, setClosing] = useState(null);
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const stats = useMemo(() => summarize(complaints), [complaints]);
  const filtered = useMemo(() => applyFilters(complaints, filters), [complaints, filters]);
  const openForm = (coords = null) => setFormCoords(coords);

  const alerts = complaints
    .filter((c) => c.status === "RESOLVED")
    .map((c) => ({ id: c.id, title: `${c.id} was resolved`, detail: "Review the proof and confirm or reopen", tone: "success", onClick: () => setSelected(c) }));

  function created(raw) {
    const c = replace(raw);
    setFormCoords(undefined);
    setPage("complaints");
    toast(`Complaint registered. Your tracking ID is ${c.id}.`);
  }

  async function reopen(c, update) {
    const reason = await confirm({ title: "Reopen this complaint?", message: "Tell the department what is still wrong. A new SLA period will start. Complaints can be reopened within 14 days of resolution, up to 3 times.", confirmLabel: "Reopen", requireText: true, textLabel: "Reason", textMinLength: 5 });
    if (!reason) return;
    try {
      const data = await api.reopen(auth, c.id, reason);
      update(replace(data.complaint));
      toast("Complaint reopened. The department has been notified.");
    } catch (err) { toast(err.message, "error"); }
  }

  async function closeWithFeedback(body) {
    const { complaint: c, update } = closing;
    try {
      const data = await api.close(auth, c.id, body);
      update(replace(data.complaint));
      setClosing(null);
      toast("Thank you! Complaint closed.");
    } catch (err) { toast(err.message, "error"); }
  }

  const actions = (c, update) => (canTransition("reopen", c.status) ? (
    <>
      <p className="ct-small">The officer marked this resolved. Is the issue fixed?</p>
      <button type="button" className="ct-btn ct-btn-primary" onClick={() => setClosing({ complaint: c, update })}><CheckCircle2 size={16} /> Yes, close it</button>
      <button type="button" className="ct-btn ct-btn-ghost" onClick={() => reopen(c, update)}><AlertTriangle size={16} /> No, reopen</button>
    </>
  ) : null);

  const reportButton = <button type="button" className="ct-btn ct-btn-primary" onClick={() => openForm()}><Plus size={17} /> Report an issue</button>;

  let body;
  if (loading && !complaints.length) body = <LoadingState label="Loading your complaints..." />;
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
  } else if (page === "map") {
    body = (
      <Panel title="My complaints on the map" subtitle="Tap anywhere on the map to report an issue at that spot.">
        <ComplaintMap complaints={complaints} onSelect={setSelected} height={480} showSearch boundaries={ref.wards} onMapClick={(coords) => openForm(coords)} />
      </Panel>
    );
  } else if (page === "track") {
    body = <TrackPanel complaints={complaints} onOpen={setSelected} />;
  } else {
    body = <ProfilePanel auth={auth} stats={stats} complaints={complaints} onLogout={onLogout} />;
  }

  return (
    <PortalShell portal="Citizen Portal" nav={NAV} active={page} onNavigate={setPage} user={auth.user} roleLabel={auth.isDemo ? "Local demo" : "Resident"}
      onLogout={onLogout} onRefresh={reload} refreshing={loading} alerts={alerts} feed={feed} unread={unread} onOpenFeed={markSeen}
      onSelectComplaint={(id) => setSelected(complaints.find((c) => c.id === id) || null)}>
      {body}
      {formCoords !== undefined && <ComplaintForm auth={auth} reference={ref} initialCoords={formCoords} onCreated={created} onCancel={() => setFormCoords(undefined)} />}
      {selected && <ComplaintDetail key={selected.id} complaint={selected} auth={auth} role="Citizen" onClose={() => setSelected(null)} renderActions={actions} />}
      {closing && <CloseDialog complaint={closing.complaint} onSubmit={closeWithFeedback} onCancel={() => setClosing(null)} />}
    </PortalShell>
  );
}
