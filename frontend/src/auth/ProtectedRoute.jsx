
import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthProvider";

export default function ProtectedRoute({
    children,
    allowedGroups = [],
}) {
    const { user, loading, session } = useAuth();

    if (loading) {
        return <div>Checking your CivicTrack session...</div>;
    }

    if (!user || !session?.tokens?.accessToken) {
        return <Navigate to="/login" replace />;
    }

    const groups =
        session.tokens.accessToken.payload["cognito:groups"] || [];

    if (
        allowedGroups.length > 0 &&
        !allowedGroups.some((group) => groups.includes(group))
    ) {
        return <Navigate to="/unauthorized" replace />;
    }

    return children;
}
