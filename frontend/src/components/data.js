import { useCallback, useEffect, useState } from "react";
import { api, slugify } from "../lib/api";
import { CATEGORY_DEPARTMENTS, DEPARTMENTS } from "../lib/lifecycle";

/** Built-in reference data, used when /reference is not reachable (older backend). */
export const BUILTIN_REFERENCE = {
  departments: DEPARTMENTS.map((name) => ({ id: slugify(name), name, active: true, builtin: true })),
  categories: Object.entries(CATEGORY_DEPARTMENTS).map(([name, dept]) => ({ name, departmentId: slugify(dept), active: true, builtin: true })),
  zones: [],
  wards: [],
  settings: { slaHours: 48, warningHours: 4, discloseOfficerName: true, maxComplaintsPerHour: 10 },
  source: "builtin-offline",
};

export function useReference(auth, { boundaries = false } = {}) {
  const [state, setState] = useState({ ref: BUILTIN_REFERENCE, loading: true, error: "" });
  const reload = useCallback(async () => {
    try {
      const ref = await api.reference(auth, boundaries);
      setState({ ref, loading: false, error: "" });
    } catch (err) {
      setState({ ref: BUILTIN_REFERENCE, loading: false, error: err.message });
    }
  }, [auth, boundaries]);
  useEffect(() => {
    const run = async () => { await reload(); };
    run();
  }, [reload]);
  return { ...state, reload };
}

const SEEN_KEY = "civictrack.notifications.seen";

function readSeen(userId) {
  try {
    return localStorage.getItem(`${SEEN_KEY}.${userId}`) || "";
  } catch {
    return "";
  }
}

/** Server-derived activity feed (complaint history) with a per-browser "last seen" marker. */
export function useNotifications(auth, refreshKey) {
  const userId = auth?.user?.userId || "anon";
  const [feed, setFeed] = useState([]);
  const [seen, setSeen] = useState(() => readSeen(userId));
  useEffect(() => {
    let cancelled = false;
    api.notifications(auth).then((d) => { if (!cancelled) setFeed(d.notifications || []); }).catch(() => {});
    return () => { cancelled = true; };
  }, [auth, refreshKey]);
  const markSeen = useCallback(() => {
    const latest = feed[0]?.at || new Date().toISOString();
    try { localStorage.setItem(`${SEEN_KEY}.${userId}`, latest); } catch { /* storage unavailable */ }
    setSeen(latest);
  }, [feed, userId]);
  const unread = feed.filter((n) => n.at > seen).length;
  return { feed, unread, markSeen };
}
