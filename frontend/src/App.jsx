import { Suspense, lazy } from "react";
import { LogOut } from "lucide-react";
import "./styles/civictrack.css";
import "./auth/login.css";
import Login from "./auth/Login";
import { useAuth } from "./auth/AuthProvider";
import { FeedbackProvider } from "./components/feedback";

// Each role downloads only its own portal.
const CitizenPortal = lazy(() => import("./citizen/CitizenPortal"));
const OfficerDashboard = lazy(() => import("./officer/OfficerDashboard"));
const AdminDashboard = lazy(() => import("./admin/AdminDashboard"));

/**
 * Chooses the portal from the user's Cognito group. This is a UX decision
 * only: every API call is authorised again by the backend.
 */
export function PortalRouter() {
  const { user, session, role, isDemo, loading, logout } = useAuth();

  if (loading) return <div className="auth-loading" role="status">Checking your CivicTrack session...</div>;
  if (!user || (!session?.tokens?.idToken && !isDemo)) return <Login />;
  const Portal = { Admin: AdminDashboard, Officer: OfficerDashboard, Citizen: CitizenPortal }[role];
  if (Portal) {
    return (
      <Suspense fallback={<div className="auth-loading" role="status">Loading your workspace...</div>}>
        <Portal onLogout={logout} />
      </Suspense>
    );
  }

  return (
    <div className="auth-loading">
      <div className="ct-panel ct-center">
        <h2>Access not assigned</h2>
        <p>Your account does not belong to a CivicTrack role. Please contact the administrator.</p>
        <button type="button" className="ct-btn ct-btn-primary" onClick={logout}><LogOut size={17} /> Sign out</button>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <FeedbackProvider>
      <PortalRouter />
    </FeedbackProvider>
  );
}
