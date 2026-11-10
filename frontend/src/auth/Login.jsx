import { useState } from "react";
import {
    signIn,
    confirmSignIn,
    signUp,
    confirmSignUp,
    resendSignUpCode,
    resetPassword,
    confirmResetPassword,
} from "aws-amplify/auth";
import { useAuth } from "./AuthProvider";
import { passwordProblem } from "../lib/validation";

const TITLES = {
    login: "Welcome back",
    register: "Create an account",
    verify: "Verify your email",
    newPassword: "Set your new password",
    forgot: "Reset your password",
    reset: "Choose a new password",
};

const SUBMIT_LABELS = {
    login: "Sign in",
    register: "Create account",
    verify: "Verify email",
    newPassword: "Set new password",
    forgot: "Send reset code",
    reset: "Reset password",
};

function Field({ id, label, ...props }) {
    return (
        <>
            <label htmlFor={id}>{label}</label>
            <input id={id} {...props} />
        </>
    );
}

export default function Login() {
    const { refreshSession, demoLogin, demoAuthEnabled } = useAuth();
    const [mode, setMode] = useState("login");
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [name, setName] = useState("");
    const [code, setCode] = useState("");
    const [newPassword, setNewPassword] = useState("");
    const [confirmNewPassword, setConfirmNewPassword] = useState("");
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    const [loading, setLoading] = useState(false);

    function changeMode(nextMode, nextMessage = "") {
        setError("");
        setMessage(nextMessage);
        setMode(nextMode);
        setNewPassword("");
        setConfirmNewPassword("");
        setCode("");
    }

    function checkNewPassword(value) {
        if (value !== confirmNewPassword) throw new Error("The new passwords do not match.");
        const problem = passwordProblem(value);
        if (problem) throw new Error(problem);
    }

    async function handleSubmit(event) {
        event.preventDefault();
        setError("");
        setMessage("");
        setLoading(true);
        try {
            if (mode === "login") {
                const result = await signIn({ username: email.trim(), password });
                if (result.isSignedIn) {
                    await refreshSession();
                } else if (result.nextStep?.signInStep === "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED") {
                    changeMode("newPassword", "Your temporary password is accepted. Please create a new permanent password.");
                } else if (result.nextStep?.signInStep === "CONFIRM_SIGN_UP") {
                    changeMode("verify", "Please verify your email address first. Enter the code we sent you.");
                } else {
                    setMessage(`Additional sign-in step required: ${result.nextStep?.signInStep || "Please continue authentication."}`);
                }
            } else if (mode === "newPassword") {
                checkNewPassword(newPassword);
                const result = await confirmSignIn({ challengeResponse: newPassword });
                if (result.isSignedIn) await refreshSession();
                else setMessage(`Additional sign-in step required: ${result.nextStep?.signInStep || "Please continue authentication."}`);
            } else if (mode === "register") {
                const problem = passwordProblem(password);
                if (problem) throw new Error(problem);
                const result = await signUp({
                    username: email.trim(),
                    password,
                    options: { userAttributes: { email: email.trim(), name: name.trim() } },
                });
                if (result.isSignUpComplete) changeMode("login", "Registration completed. You can now sign in.");
                else if (result.nextStep?.signUpStep === "CONFIRM_SIGN_UP") changeMode("verify", "A verification code has been sent to your email.");
                else setMessage("Registration requires an additional step.");
            } else if (mode === "verify") {
                await confirmSignUp({ username: email.trim(), confirmationCode: code.trim() });
                setPassword("");
                changeMode("login", "Email verified successfully! You can now sign in.");
            } else if (mode === "forgot") {
                await resetPassword({ username: email.trim() });
                // Same message whether or not the account exists (no user enumeration).
                changeMode("reset", "If an account exists for this email, a reset code has been sent.");
            } else if (mode === "reset") {
                checkNewPassword(newPassword);
                await confirmResetPassword({ username: email.trim(), confirmationCode: code.trim(), newPassword });
                setPassword("");
                changeMode("login", "Password updated. Sign in with your new password.");
            }
        } catch (err) {
            setError(err.message || "Something went wrong.");
        } finally {
            setLoading(false);
        }
    }

    async function handleResendCode() {
        setError("");
        setMessage("");
        setLoading(true);
        try {
            await resendSignUpCode({ username: email.trim() });
            setMessage("A new verification code has been sent.");
        } catch (err) {
            setError(err.message || "Unable to resend verification code.");
        } finally {
            setLoading(false);
        }
    }

    const needsEmail = ["login", "register", "forgot", "newPassword"].includes(mode);
    const needsCode = mode === "verify" || mode === "reset";
    const needsNewPassword = mode === "newPassword" || mode === "reset";

    return (
        <div className="login-page">
            <style>{`
                .demo-login-panel { margin-top: 22px; padding-top: 17px; border-top: 1px solid #e7edf5; }
                .demo-login-divider { display: flex; align-items: center; gap: 10px; color: #8492a6; font-size: 10px; font-weight: 800; letter-spacing: 1.2px; }
                .demo-login-panel > p { color: #738198; font-size: 12px; text-align: center; margin: 10px 0 12px; }
                .demo-login-buttons { display: grid; gap: 8px; }
                .demo-login-buttons button { width: 100%; padding: 10px 12px; border: 1px solid #dce5f1; border-radius: 8px; background: #f7faff; color: #234b86; font-size: 12px; font-weight: 700; cursor: pointer; }
                .demo-login-buttons button:hover { background: #eaf2ff; border-color: #a9c4ed; }
                .demo-login-panel small { display: block; margin-top: 10px; color: #929eae; font-size: 10px; line-height: 1.5; text-align: center; }
                .login-hint { font-size: 12px; color: #6b7a90; margin: -4px 0 6px; }
            `}</style>
            <div className="login-card">
                <h1>CivicTrack</h1>
                <p>Citizen Services Platform</p>
                <h2>{TITLES[mode]}</h2>
                <form onSubmit={handleSubmit} noValidate={false}>
                    {mode === "register" && (
                        <Field id="ct-name" label="Full name" required autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Enter your full name" />
                    )}
                    {needsEmail && (
                        <Field id="ct-email" label="Email address" type="email" required autoComplete="email" disabled={mode === "newPassword"}
                            value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Enter your email" />
                    )}
                    {(mode === "login" || mode === "register") && (
                        <>
                            <Field id="ct-password" label="Password" type="password" required minLength={8}
                                autoComplete={mode === "login" ? "current-password" : "new-password"}
                                value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Enter your password" />
                            {mode === "register" && <p className="login-hint">At least 8 characters with upper- and lower-case letters, a number and a symbol.</p>}
                        </>
                    )}
                    {mode === "newPassword" && <p>Your administrator provided a temporary password. Create a permanent password to continue.</p>}
                    {needsCode && (
                        <>
                            <p>Enter the code sent to {email || "your email"}.</p>
                            <Field id="ct-code" label={mode === "reset" ? "Reset code" : "Verification code"} required inputMode="numeric" autoComplete="one-time-code"
                                value={code} onChange={(e) => setCode(e.target.value)} placeholder="6-digit code" />
                        </>
                    )}
                    {needsNewPassword && (
                        <>
                            <Field id="ct-new-password" label="New password" type="password" required minLength={8} autoComplete="new-password"
                                value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="Enter your new password" />
                            <Field id="ct-confirm-password" label="Confirm new password" type="password" required minLength={8} autoComplete="new-password"
                                value={confirmNewPassword} onChange={(e) => setConfirmNewPassword(e.target.value)} placeholder="Re-enter your new password" />
                        </>
                    )}
                    {error && <p role="alert">{error}</p>}
                    {message && <p role="status">{message}</p>}
                    <button type="submit" disabled={loading}>{loading ? "Please wait..." : SUBMIT_LABELS[mode]}</button>
                </form>
                {mode === "login" && <button type="button" onClick={() => changeMode("forgot")}>Forgot your password?</button>}
                {mode === "verify" && (
                    <button type="button" disabled={loading} onClick={handleResendCode}>Resend verification code</button>
                )}
                {mode === "reset" && (
                    <button type="button" disabled={loading} onClick={() => changeMode("forgot")}>Send a new code</button>
                )}
                {["verify", "newPassword", "forgot", "reset"].includes(mode) && (
                    <button type="button" onClick={() => changeMode("login")}>Back to sign in</button>
                )}
                {(mode === "login" || mode === "register") && (
                    <button type="button" onClick={() => changeMode(mode === "login" ? "register" : "login")}>
                        {mode === "login" ? "New citizen? Create an account" : "Already registered? Sign in"}
                    </button>
                )}
                {demoAuthEnabled && mode === "login" && (
                    <div className="demo-login-panel">
                        <div className="demo-login-divider"><span>LOCAL PREVIEW</span></div>
                        <p>Preview a portal without connecting to AWS.</p>
                        <div className="demo-login-buttons">
                            <button type="button" onClick={() => demoLogin("Citizen")}>Preview Citizen Portal</button>
                            <button type="button" onClick={() => demoLogin("Officer")}>Preview Officer Portal</button>
                            <button type="button" onClick={() => demoLogin("Admin")}>Preview Admin Portal</button>
                        </div>
                        <small>Demo accounts are local-only and are not real user accounts.</small>
                    </div>
                )}
            </div>
        </div>
    );
}
