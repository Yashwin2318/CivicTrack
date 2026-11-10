import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOW, SAMPLE, raw } from "./fixtures";

const authState = { current: null };
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => authState.current }));
vi.mock("../components/ComplaintMap", () => ({
  default: ({ complaints }) => <div data-testid="map">{complaints.length} on map</div>,
  LocationPicker: () => <div data-testid="picker" />,
  MiniMap: () => <div data-testid="minimap" />,
  STATUS_COLORS: {},
}));
const amplify = vi.hoisted(() => ({ resetPassword: vi.fn(), confirmResetPassword: vi.fn(), signIn: vi.fn(), signUp: vi.fn(),
  confirmSignIn: vi.fn(), confirmSignUp: vi.fn(), resendSignUpCode: vi.fn(), updatePassword: vi.fn(), updateUserAttributes: vi.fn() }));
vi.mock("aws-amplify/auth", () => amplify);

const apiMock = vi.hoisted(() => Object.fromEntries([
  "listComplaints", "getComplaint", "createComplaint", "listOfficers", "assign", "unassign", "updateDetails", "start", "resolve", "reopen", "close",
  "uploadOfficerPhoto", "accept", "decline", "progress", "reject", "escalate", "similar", "notifications", "me", "analytics", "reference",
  "initializeReference", "saveDepartment", "saveCategory", "saveZone", "saveWard", "importBoundaries", "saveSettings", "auditLog",
  "saveOfficerProfile", "listUsers", "createUser", "updateUser",
].map((name) => [name, vi.fn()])));
vi.mock("../lib/api", async (importOriginal) => ({ ...(await importOriginal()), api: apiMock, uploadEvidence: vi.fn() }));

import App from "../App";
import Login from "../auth/Login";

const REFERENCE = {
  departments: [{ id: "public-works", name: "Public Works", active: true }, { id: "electrical", name: "Electrical", active: true }],
  categories: [{ name: "Roads", departmentId: "public-works", active: true }, { name: "Streetlights", departmentId: "electrical", active: true }],
  zones: [{ id: "east", name: "East", active: true }],
  wards: [{ id: "w80", name: "Ward 80", zoneId: "east", active: true }],
  settings: { slaHours: 48, warningHours: 4, discloseOfficerName: true, maxComplaintsPerHour: 10 },
  source: "stored",
};

function signIn(role) {
  authState.current = { user: { username: `${role.toLowerCase()}@example.test`, userId: `${role}-1` },
    session: { tokens: { idToken: { toString: () => "token", payload: { name: "Test" } } } }, role, isDemo: false, loading: false, logout: vi.fn(),
    refreshSession: vi.fn(), demoLogin: vi.fn(), demoAuthEnabled: false };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  Object.values(apiMock).forEach((fn) => fn.mockReset());
  Object.values(amplify).forEach((fn) => fn.mockReset());
  apiMock.listComplaints.mockResolvedValue({ complaints: SAMPLE });
  apiMock.getComplaint.mockRejectedValue(new Error("not deployed"));
  apiMock.reference.mockResolvedValue(REFERENCE);
  apiMock.notifications.mockResolvedValue({ notifications: [] });
  apiMock.me.mockResolvedValue({ user: { role: "Officer", profile: { departmentName: "Public Works", zoneNames: ["East"], wardNames: [], available: true, maxActive: 10 } } });
  apiMock.analytics.mockRejectedValue(new Error("not deployed"));
  apiMock.similar.mockResolvedValue({ duplicates: [], suggestedCategory: { category: null } });
  apiMock.listOfficers.mockResolvedValue({ source: "cognito", officers: [
    { sub: "officer-1", name: "Officer One", email: "o1@example.test", enabled: true, profile: { departmentId: "public-works", zoneIds: ["east"], wardIds: [], available: true, maxActive: 10 } },
    { sub: "officer-2", name: "Officer Two", email: "o2@example.test", enabled: true, profile: { departmentId: "electrical", zoneIds: [], wardIds: [], available: false, maxActive: 10 } },
  ] });
  apiMock.listUsers.mockResolvedValue({ users: [{ username: "u1", email: "o1@example.test", name: "Officer One", enabled: true, status: "CONFIRMED" }], nextToken: null });
  apiMock.auditLog.mockResolvedValue({ month: "2026-11", entries: [{ at: "2026-11-09T10:00:00Z", action: "user_created", target: "abc", actorRole: "Admin", actorSub: "admin-1-xyz", details: { role: "Officer" } }] });
});

afterEach(() => vi.useRealTimers());

async function openAdmin(page) {
  signIn("Admin");
  const user = userEvent.setup();
  render(<App />);
  await screen.findByText(/^Administration \//, {}, { timeout: 5000 });
  if (page) await user.click(screen.getByRole("button", { name: new RegExp(`^${page}`, "i") }));
  return user;
}

describe("authentication flows", () => {
  it("resets a forgotten password and enforces the password policy", async () => {
    authState.current = { refreshSession: vi.fn(), demoLogin: vi.fn(), demoAuthEnabled: false };
    amplify.resetPassword.mockResolvedValue({});
    amplify.confirmResetPassword.mockResolvedValue({});
    const user = userEvent.setup();
    render(<Login />);
    await user.click(screen.getByRole("button", { name: /forgot your password/i }));
    await user.type(screen.getByLabelText(/email address/i), "asha@example.test");
    await user.click(screen.getByRole("button", { name: /send reset code/i }));
    expect(amplify.resetPassword).toHaveBeenCalledWith({ username: "asha@example.test" });
    expect(await screen.findByText(/if an account exists/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText(/reset code/i), "123456");
    await user.type(screen.getByLabelText(/^new password/i), "weakpass");
    await user.type(screen.getByLabelText(/confirm new password/i), "weakpass");
    await user.click(screen.getByRole("button", { name: /^reset password/i }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/upper- and lower-case/);
    expect(amplify.confirmResetPassword).not.toHaveBeenCalled();
    await user.clear(screen.getByLabelText(/^new password/i));
    await user.clear(screen.getByLabelText(/confirm new password/i));
    await user.type(screen.getByLabelText(/^new password/i), "Str0ng!Pass");
    await user.type(screen.getByLabelText(/confirm new password/i), "Str0ng!Pass");
    await user.click(screen.getByRole("button", { name: /^reset password/i }));
    await waitFor(() => expect(amplify.confirmResetPassword).toHaveBeenCalledWith({ username: "asha@example.test", confirmationCode: "123456", newPassword: "Str0ng!Pass" }));
    expect(await screen.findByText(/password updated/i)).toBeInTheDocument();
  });

  it("labels every login field for assistive technology", () => {
    authState.current = { refreshSession: vi.fn(), demoLogin: vi.fn(), demoAuthEnabled: false };
    render(<Login />);
    expect(screen.getByLabelText(/email address/i)).toHaveAttribute("type", "email");
    expect(screen.getByLabelText(/^password/i)).toHaveAttribute("autocomplete", "current-password");
  });
});

describe("citizen intelligence and feedback", () => {
  beforeEach(() => signIn("Citizen"));

  it("warns about nearby duplicates and suggests a category before submitting", async () => {
    apiMock.similar.mockResolvedValue({ duplicates: [{ distanceMeters: 40, status: "PENDING", category: "Roads", yours: false }], suggestedCategory: { category: "Streetlights" } });
    apiMock.createComplaint.mockResolvedValue({ complaint: raw({ complaintId: "CT-NEW0000001", category: "Streetlights" }) });
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /report an issue/i }, { timeout: 5000 }))[0]);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText(/routed to: public works/i)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/issue title/i), "Lamp post dark");
    await user.type(within(dialog).getByLabelText(/address/i), "MG Road");
    await user.type(within(dialog).getByLabelText(/description/i), "Street light not working at night");
    await user.click(within(dialog).getByRole("button", { name: /submit complaint/i }));
    expect(await within(dialog).findByText(/1 open complaint\(s\) about roads already exist/i)).toBeInTheDocument();
    expect(apiMock.createComplaint).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: /use streetlights/i }));
    expect(within(dialog).getByLabelText(/category/i)).toHaveValue("Streetlights");
    await user.click(within(dialog).getByRole("button", { name: /submit complaint/i })); // re-check after change
    await user.click(await within(dialog).findByRole("button", { name: /submit anyway/i }));
    await waitFor(() => expect(apiMock.createComplaint).toHaveBeenCalledTimes(1));
    expect(apiMock.createComplaint.mock.calls[0][1].category).toBe("Streetlights");
  });

  it("closes a resolved complaint with a rating and feedback", async () => {
    apiMock.listComplaints.mockResolvedValue({ complaints: [raw({ complaintId: "CT-RESOLVED01", status: "RESOLVED", resolvedAt: new Date().toISOString(), title: "Fixed light" })] });
    apiMock.close.mockResolvedValue({ complaint: raw({ complaintId: "CT-RESOLVED01", status: "CLOSED", title: "Fixed light", citizenRating: 4 }) });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /fixed light/i }, { timeout: 5000 }));
    await user.click(screen.getByRole("button", { name: /yes, close it/i }));
    const dialog = screen.getByRole("dialog", { name: /confirm the issue is fixed/i });
    fireEvent.click(within(dialog).getByLabelText("4 stars"));
    await user.type(within(dialog).getByLabelText(/feedback/i), "Good job");
    await user.click(within(dialog).getByRole("button", { name: /close complaint/i }));
    await waitFor(() => expect(apiMock.close).toHaveBeenCalledWith(expect.anything(), "CT-RESOLVED01", { rating: 4, feedback: "Good job" }));
  });

  it("shows server notifications in the notification centre", { timeout: 15000 }, async () => {
    apiMock.notifications.mockResolvedValue({ notifications: [{ complaintId: "CT-AAAAAAAAA1", title: "Pothole", action: "work_started", at: new Date().toISOString(), officerName: "Officer One" }] });
    const user = userEvent.setup();
    render(<App />);
    const bell = await screen.findByRole("button", { name: /notifications \(2\)/i }, { timeout: 5000 }); // 1 resolved alert + 1 unread
    await user.click(bell);
    expect(screen.getByRole("dialog", { name: /notifications/i })).toHaveTextContent(/work started: CT-AAAAAAAAA1/i);
  });
});

describe("officer assignment responses", () => {
  beforeEach(() => signIn("Officer"));

  it("accepts and declines with a mandatory reason", async () => {
    apiMock.accept.mockResolvedValue({ complaint: { ...SAMPLE[1], acceptedAt: new Date().toISOString() } });
    apiMock.decline.mockResolvedValue({ complaint: { ...SAMPLE[1], status: "PENDING", assignedOfficerSub: "" } });
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /^accept$/i }, { timeout: 5000 }))[0]);
    await waitFor(() => expect(apiMock.accept).toHaveBeenCalledWith(expect.anything(), "CT-AAAAAAAAA2"));
    await user.click(screen.getAllByRole("button", { name: /^decline$/i })[0]);
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByRole("textbox"), "too short");
    expect(within(dialog).getByRole("button", { name: /^decline$/i })).toBeDisabled();
    await user.type(within(dialog).getByRole("textbox"), " — outside my zone");
    await user.click(within(dialog).getByRole("button", { name: /^decline$/i }));
    await waitFor(() => expect(apiMock.decline).toHaveBeenCalledWith(expect.anything(), "CT-AAAAAAAAA2", "too short — outside my zone"));
  });

  it("shows department and zone from the officer profile", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /my profile/i }, { timeout: 5000 }));
    expect(await screen.findByText("Public Works")).toBeInTheDocument();
    expect(screen.getByText("East")).toBeInTheDocument();
  });

  it("never shows administration navigation to officers", async () => {
    render(<App />);
    await screen.findByText(/^Officer Portal \//, {}, { timeout: 5000 });
    for (const label of [/users & roles/i, /audit log/i, /zones & wards/i, /settings/i]) {
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument();
    }
  });
});

describe("admin management", () => {
  it("renders every admin page without errors", async () => {
    const user = await openAdmin();
    for (const label of ["Complaints", "City Map", "Analytics & Reports", "Officers", "Users & Roles", "Departments", "Zones & Wards", "Settings", "Audit Log", "Overview"]) {
      await user.click(screen.getByRole("button", { name: new RegExp(`^${label.replace(/[&]/g, "\\&")}`, "i") }));
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(label);
    }
    expect(screen.queryByText(/something went wrong/i)).not.toBeInTheDocument();
  });

  it("ranks the available same-department officer first when assigning", async () => {
    const user = await openAdmin("Complaints");
    await user.click(screen.getAllByRole("button", { name: /^assign$/i })[0]);
    const options = within(screen.getByRole("dialog", { name: /assign complaint/i })).getAllByRole("radio");
    expect(options[0].closest("label")).toHaveTextContent(/Officer One.*Suggested.*same department/);
    expect(options[1].closest("label")).toHaveTextContent(/marked unavailable/);
  });

  it("rejects a complaint only with a reason", async () => {
    apiMock.reject.mockResolvedValue({ complaint: { ...SAMPLE[0], status: "REJECTED", rejectionReason: "Duplicate of a known issue" } });
    const user = await openAdmin("Complaints");
    await user.click(screen.getAllByRole("button", { name: /^CT-AAAAAAAAA1$/ })[0]);
    await user.click(screen.getByRole("button", { name: /^reject$/i }));
    const dialog = screen.getByRole("dialog", { name: /close without action/i });
    await user.click(within(dialog).getByRole("button", { name: /reject complaint/i }));
    expect(within(dialog).getByRole("alert")).toHaveTextContent(/at least 10 characters/);
    await user.type(within(dialog).getByLabelText(/reason shown/i), "Duplicate of a known issue");
    await user.click(within(dialog).getByRole("button", { name: /reject complaint/i }));
    await waitFor(() => expect(apiMock.reject).toHaveBeenCalledWith(expect.anything(), "CT-AAAAAAAAA1", "Duplicate of a known issue", undefined));
  });

  it("creates a department with a generated ID", async () => {
    apiMock.saveDepartment.mockResolvedValue({});
    const user = await openAdmin("Departments");
    await user.click(screen.getByRole("button", { name: /add department/i }));
    const dialog = screen.getByRole("dialog", { name: /add department/i });
    await user.type(within(dialog).getByLabelText(/^name/i), "Parks & Gardens");
    await user.click(within(dialog).getByRole("button", { name: /^save$/i }));
    await waitFor(() => expect(apiMock.saveDepartment).toHaveBeenCalledWith(expect.anything(), "parks-gardens", { name: "Parks & Gardens", description: "", active: true }));
  });

  it("imports a GeoJSON ward file with the chosen name property", async () => {
    apiMock.importBoundaries.mockResolvedValue({ imported: [{ id: "ward-a", name: "Ward A" }], skipped: [] });
    const user = await openAdmin("Zones & Wards");
    const geojson = { type: "FeatureCollection", features: [{ type: "Feature", properties: { WARD_NAME: "Ward A", WARD_NO: 1 }, geometry: { type: "Polygon", coordinates: [[[77.6, 12.9], [77.7, 12.9], [77.7, 13], [77.6, 12.9]]] } }] };
    const file = new File([JSON.stringify(geojson)], "wards.geojson", { type: "application/geo+json" });
    await user.upload(screen.getByLabelText(/geojson file/i), file);
    expect(await screen.findByText(/wards.geojson: 1 feature/)).toBeInTheDocument();
    expect(screen.getByLabelText(/name property/i)).toHaveValue("WARD_NAME");
    await user.click(screen.getByRole("button", { name: /^import$/i }));
    await waitFor(() => expect(apiMock.importBoundaries).toHaveBeenCalled());
    const body = apiMock.importBoundaries.mock.calls[0][1];
    expect(body).toMatchObject({ level: "ward", nameProperty: "WARD_NAME", numberProperty: "WARD_NO" });
  });

  it("invites staff and lists audit entries", async () => {
    apiMock.createUser.mockResolvedValue({ user: { email: "new@example.test", role: "Officer" } });
    const user = await openAdmin("Users & Roles");
    expect(await screen.findByText("o1@example.test")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /invite staff/i }));
    const dialog = screen.getByRole("dialog", { name: /invite staff/i });
    await user.type(within(dialog).getByLabelText(/full name/i), "New Officer");
    await user.type(within(dialog).getByLabelText(/email/i), "new@example.test");
    await user.click(within(dialog).getByRole("button", { name: /send invitation/i }));
    await waitFor(() => expect(apiMock.createUser).toHaveBeenCalledWith(expect.anything(), { email: "new@example.test", name: "New Officer", role: "Officer" }));
    await user.click(screen.getByRole("button", { name: /^audit log/i }));
    expect(await screen.findByText("user created")).toBeInTheDocument();
  });

  it("saves SLA settings", async () => {
    apiMock.saveSettings.mockResolvedValue({ settings: { ...REFERENCE.settings, slaHours: 24 } });
    const user = await openAdmin("Settings");
    const sla = screen.getByLabelText(/sla resolution target/i);
    await user.clear(sla);
    await user.type(sla, "24");
    await user.click(screen.getByRole("button", { name: /save settings/i }));
    await waitFor(() => expect(apiMock.saveSettings).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ slaHours: 24, warningHours: 4 })));
  });

  it("uses server-side analytics when available", async () => {
    apiMock.analytics.mockResolvedValue({
      generatedAt: new Date().toISOString(), granularity: "day", mapped: 0, hotspots: [],
      summary: { total: 1234, open: 10, resolved: 1200, rejected: 24, overdue: 3, warning: 1, newInPeriod: 50, resolutionRate: 0.99, avgResolutionHours: 20, medianResolutionHours: 18, slaComplianceRate: 0.9, slaMet: 900, slaEvaluated: 1000, reopenedEver: 5, escalated: 7, byStatus: { PENDING: 4, IN_PROGRESS: 6 } },
      byDepartment: [], byCategory: [], byPriority: [], byZone: [], byWard: [], byOfficer: [], trend: [],
    });
    await openAdmin("Analytics");
    expect(await screen.findByText("1234")).toBeInTheDocument();
    expect(screen.getByText(/server-side aggregation/i)).toBeInTheDocument();
    expect(apiMock.analytics).toHaveBeenCalledWith(expect.anything(), expect.objectContaining({ granularity: "day" }));
  });

  it("falls back to browser analytics and says so", async () => {
    await openAdmin("Analytics");
    expect(await screen.findByText(/computed in the browser/i)).toBeInTheDocument();
  });
});
