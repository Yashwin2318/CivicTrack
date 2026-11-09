/* eslint-disable react-refresh/only-export-components */
import {
    createContext,
    useContext,
    useEffect,
    useState,
} from "react";

import {
    getCurrentUser,
    fetchAuthSession,
    signOut,
} from "aws-amplify/auth";

const AuthContext = createContext(null);

const DEMO_AUTH_ENABLED =
    import.meta.env.DEV && import.meta.env.VITE_ENABLE_DEMO_AUTH === "true";

function getUserRole(session) {
    const groups =
        session?.tokens?.idToken?.payload?.["cognito:groups"] ||
        session?.tokens?.accessToken?.payload?.["cognito:groups"] ||
        [];

    if (groups.includes("Admin")) return "Admin";
    if (groups.includes("Officer")) return "Officer";
    if (groups.includes("Citizen")) return "Citizen";
    return "Unassigned";
}

function createDemoToken(role, tokenType) {
    return {
        payload: {
            sub: `local-demo-${role.toLowerCase()}`,
            email: `demo.${role.toLowerCase()}@civictrack.local`,
            "cognito:groups": [role],
            token_use: tokenType,
        },
        toString: () => `LOCAL-DEMO-${tokenType.toUpperCase()}-${role.toUpperCase()}`,
    };
}

function createDemoSession(role) {
    return {
        tokens: {
            idToken: createDemoToken(role, "id"),
            accessToken: createDemoToken(role, "access"),
        },
    };
}

export function AuthProvider({ children }) {
    const [user, setUser] = useState(null);
    const [session, setSession] = useState(null);
    const [role, setRole] = useState(null);
    const [isDemo, setIsDemo] = useState(false);
    const [loading, setLoading] = useState(true);

    async function refreshSession() {
        try {
            const currentUser = await getCurrentUser();
            const authSession = await fetchAuthSession();
            setUser(currentUser);
            setSession(authSession);
            setRole(getUserRole(authSession));
            setIsDemo(false);
        } catch {
            setUser(null);
            setSession(null);
            setRole(null);
            setIsDemo(false);
        } finally {
            setLoading(false);
        }
    }

    useEffect(() => {
        const restore = async () => { await refreshSession(); };
        restore();
    }, []);

    function demoLogin(selectedRole) {
        if (!DEMO_AUTH_ENABLED) return;

        const normalizedRole = ["Citizen", "Officer", "Admin"].includes(selectedRole)
            ? selectedRole
            : "Citizen";

        setUser({
            username: `demo.${normalizedRole.toLowerCase()}@civictrack.local`,
            userId: `local-demo-${normalizedRole.toLowerCase()}`,
        });
        setSession(createDemoSession(normalizedRole));
        setRole(normalizedRole);
        setIsDemo(true);
        setLoading(false);
    }

    async function logout() {
        const wasDemo = isDemo;

        try {
            if (!wasDemo) await signOut();
        } finally {
            setUser(null);
            setSession(null);
            setRole(null);
            setIsDemo(false);
            setLoading(false);
        }
    }

    return (
        <AuthContext.Provider
            value={{
                user,
                session,
                role,
                isDemo,
                demoAuthEnabled: DEMO_AUTH_ENABLED,
                loading,
                logout,
                refreshSession,
                demoLogin,
            }}
        >
            {children}
        </AuthContext.Provider>
    );
}

export function useAuth() {
    return useContext(AuthContext);
}
