
import { useState } from "react";
import {
    signIn,
    confirmSignIn,
    signUp,
    confirmSignUp,
    resendSignUpCode,
} from "aws-amplify/auth";
import { useAuth } from "./AuthProvider";

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

    async function handleSubmit(event) {
        event.preventDefault();
        setError("");
        setMessage("");
        setLoading(true);

        try {
            if (mode === "login") {
                const result = await signIn({
                    username: email.trim(),
                    password,
                });

                if (result.isSignedIn) {
                    await refreshSession();
                } else if (
                    result.nextStep?.signInStep ===
                    "CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED"
                ) {
                    setMode("newPassword");
                    setMessage(
                        "Your temporary password is accepted. Please create a new permanent password."
                    );
                } else {
                    setMessage(
                        `Additional sign-in step required: ${result.nextStep?.signInStep ||
                        "Please continue authentication."
                        }`
                    );
                }
            }

            if (mode === "newPassword") {
                if (newPassword !== confirmNewPassword) {
                    throw new Error("The new passwords do not match.");
                }

                if (newPassword.length < 8) {
                    throw new Error(
                        "Your new password must contain at least 8 characters."
                    );
                }

                const result = await confirmSignIn({
                    challengeResponse: newPassword,
                });

                if (result.isSignedIn) {
                    await refreshSession();
                } else {
                    setMessage(
                        `Additional sign-in step required: ${result.nextStep?.signInStep ||
                        "Please continue authentication."
                        }`
                    );
                }
            }

            if (mode === "register") {
                const result = await signUp({
                    username: email.trim(),
                    password,
                    options: {
                        userAttributes: {
                            email: email.trim(),
                            name,
                        },
                    },
                });

                if (result.isSignUpComplete) {
                    setMode("login");
                    setMessage(
                        "Registration completed. You can now sign in."
                    );
                } else if (
                    result.nextStep?.signUpStep === "CONFIRM_SIGN_UP"
                ) {
                    setMode("verify");
                    setMessage(
                        "A verification code has been sent to your email."
                    );
                } else {
                    setMessage(
                        "Registration requires an additional step."
                    );
                }
            }

            if (mode === "verify") {
                await confirmSignUp({
                    username: email.trim(),
                    confirmationCode: code,
                });

                setMode("login");
                setCode("");
                setPassword("");
                setMessage(
                    "Email verified successfully! You can now sign in."
                );
            }
        } catch (err) {
            console.error("AUTHENTICATION ERROR:", err);
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
            setError(
                err.message || "Unable to resend verification code."
            );
        } finally {
            setLoading(false);
        }
    }

    function changeMode(nextMode) {
        setError("");
        setMessage("");
        setMode(nextMode);
        setNewPassword("");
        setConfirmNewPassword("");
    }

    return (
        <div className="login-page">
            <style>{`
                .demo-login-panel {
                    margin-top: 22px;
                    padding-top: 17px;
                    border-top: 1px solid #e7edf5;
                }

                .demo-login-divider {
                    display: flex;
                    align-items: center;
                    gap: 10px;
                    color: #8492a6;
                    font-size: 10px;
                    font-weight: 800;
                    letter-spacing: 1.2px;
                }

                .demo-login-panel > p {
                    color: #738198;
                    font-size: 12px;
                    text-align: center;
                    margin: 10px 0 12px;
                }

                .demo-login-buttons {
                    display: grid;
                    gap: 8px;
                }

                .demo-login-buttons button {
                    width: 100%;
                    padding: 10px 12px;
                    border: 1px solid #dce5f1;
                    border-radius: 8px;
                    background: #f7faff;
                    color: #234b86;
                    font-size: 12px;
                    font-weight: 700;
                    cursor: pointer;
                }

                .demo-login-buttons button:hover {
                    background: #eaf2ff;
                    border-color: #a9c4ed;
                }

                .demo-login-panel small {
                    display: block;
                    margin-top: 10px;
                    color: #929eae;
                    font-size: 10px;
                    line-height: 1.5;
                    text-align: center;
                }
            `}</style>

            <div className="login-card">
                <h1>CivicTrack</h1>
                <p>Citizen Services Platform</p>

                <h2>
                    {mode === "login" && "Welcome back"}
                    {mode === "register" && "Create an account"}
                    {mode === "verify" && "Verify your email"}
                    {mode === "newPassword" && "Set your new password"}
                </h2>

                <form onSubmit={handleSubmit}>
                    {mode === "register" && (
                        <>
                            <label>Full name</label>
                            <input
                                required
                                value={name}
                                onChange={(event) =>
                                    setName(event.target.value)
                                }
                                placeholder="Enter your full name"
                            />
                        </>
                    )}

                    {mode !== "verify" && (
                        <>
                            <label>Email address</label>
                            <input
                                type="email"
                                required
                                disabled={mode === "newPassword"}
                                value={email}
                                onChange={(event) =>
                                    setEmail(event.target.value)
                                }
                                placeholder="Enter your email"
                            />
                        </>
                    )}

                    {(mode === "login" || mode === "register") && (
                        <>
                            <label>Password</label>
                            <input
                                type="password"
                                required
                                minLength={8}
                                value={password}
                                onChange={(event) =>
                                    setPassword(event.target.value)
                                }
                                placeholder="Enter your password"
                            />
                        </>
                    )}

                    {mode === "newPassword" && (
                        <>
                            <p>
                                Your administrator provided a temporary
                                password. Create a permanent password to
                                continue.
                            </p>

                            <label>New password</label>
                            <input
                                type="password"
                                required
                                minLength={8}
                                value={newPassword}
                                onChange={(event) =>
                                    setNewPassword(event.target.value)
                                }
                                placeholder="Enter your new password"
                            />

                            <label>Confirm new password</label>
                            <input
                                type="password"
                                required
                                minLength={8}
                                value={confirmNewPassword}
                                onChange={(event) =>
                                    setConfirmNewPassword(event.target.value)
                                }
                                placeholder="Re-enter your new password"
                            />
                        </>
                    )}

                    {mode === "verify" && (
                        <>
                            <p>
                                Enter the verification code sent to {email}.
                            </p>

                            <label>Verification code</label>
                            <input
                                required
                                value={code}
                                onChange={(event) =>
                                    setCode(event.target.value)
                                }
                                placeholder="Enter verification code"
                            />
                        </>
                    )}

                    {error && <p role="alert">{error}</p>}
                    {message && <p role="status">{message}</p>}

                    <button type="submit" disabled={loading}>
                        {loading
                            ? "Please wait..."
                            : mode === "login"
                                ? "Sign in"
                                : mode === "register"
                                    ? "Create account"
                                    : mode === "verify"
                                        ? "Verify email"
                                        : "Set new password"}
                    </button>
                </form>

                {mode === "verify" && (
                    <>
                        <button
                            type="button"
                            disabled={loading}
                            onClick={handleResendCode}
                        >
                            Resend verification code
                        </button>

                        <button
                            type="button"
                            onClick={() => changeMode("login")}
                        >
                            Back to sign in
                        </button>
                    </>
                )}

                {mode === "newPassword" && (
                    <button
                        type="button"
                        onClick={() => changeMode("login")}
                    >
                        Back to sign in
                    </button>
                )}

                {mode !== "verify" && mode !== "newPassword" && (
                    <button
                        type="button"
                        onClick={() =>
                            changeMode(
                                mode === "login" ? "register" : "login"
                            )
                        }
                    >
                        {mode === "login"
                            ? "New citizen? Create an account"
                            : "Already registered? Sign in"}
                    </button>
                )}

                {demoAuthEnabled && mode === "login" && (
                    <div className="demo-login-panel">
                        <div className="demo-login-divider">
                            <span>LOCAL PREVIEW</span>
                        </div>

                        <p>
                            Preview a portal without connecting to AWS.
                        </p>

                        <div className="demo-login-buttons">
                            <button
                                type="button"
                                onClick={() => demoLogin("Citizen")}
                            >
                                Preview Citizen Portal
                            </button>

                            <button
                                type="button"
                                onClick={() => demoLogin("Officer")}
                            >
                                Preview Officer Portal
                            </button>

                            <button
                                type="button"
                                onClick={() => demoLogin("Admin")}
                            >
                                Preview Admin Portal
                            </button>
                        </div>

                        <small>
                            Demo accounts are local-only and are not real
                            user accounts.
                        </small>
                    </div>
                )}
            </div>
        </div>
    );
}
