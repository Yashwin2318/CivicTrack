import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NOW, SAMPLE, raw } from "./fixtures";

// ---- mocks -------------------------------------------------------------
const authState = { current: null };
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => authState.current }));
vi.mock("../auth/Login", () => ({ default: () => <div>Login screen</div> }));
vi.mock("../components/ComplaintMap", () => ({
  default: ({ complaints }) => <div data-testid="map">{complaints.length} on map</div>,
  LocationPicker: () => <div data-testid="picker" />,
  MiniMap: () => <div data-testid="minimap" />,
  STATUS_COLORS: {},
}));

const apiMock = vi.hoisted(() => ({
  listComplaints: vi.fn(),
  getComplaint: vi.fn(),
  createComplaint: vi.fn(),
  listOfficers: vi.fn(),
  assign: vi.fn(),
  unassign: vi.fn(),
  updateDetails: vi.fn(),
  start: vi.fn(),
  resolve: vi.fn(),
  reopen: vi.fn(),
  close: vi.fn(),
  uploadOfficerPhoto: vi.fn(),
  accept: vi.fn(),
  decline: vi.fn(),
  progress: vi.fn(),
  reject: vi.fn(),
  escalate: vi.fn(),
  similar: vi.fn(),
  notifications: vi.fn(),
  me: vi.fn(),
  analytics: vi.fn(),
  reference: vi.fn(),
  initializeReference: vi.fn(),
  saveDepartment: vi.fn(),
  saveCategory: vi.fn(),
  saveZone: vi.fn(),
  saveWard: vi.fn(),
  importBoundaries: vi.fn(),
  saveSettings: vi.fn(),
  auditLog: vi.fn(),
  saveOfficerProfile: vi.fn(),
  listUsers: vi.fn(),
  createUser: vi.fn(),
  updateUser: vi.fn(),
}));
vi.mock("../lib/api", async (importOriginal) => ({ ...(await importOriginal()), api: apiMock, uploadEvidence: vi.fn() }));

import App from "../App";

function signIn(role) {
  authState.current = {
    user: { username: `${(role || "x").toLowerCase()}@example.test`, userId: "u1" },
    session: { tokens: { idToken: { toString: () => "token" } } },
    role,
    isDemo: false,
    loading: false,
    logout: vi.fn(),
  };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  Object.values(apiMock).forEach((fn) => fn.mockReset());
  apiMock.listComplaints.mockResolvedValue({ complaints: SAMPLE });
  apiMock.getComplaint.mockRejectedValue(new Error("not deployed"));
  apiMock.reference.mockRejectedValue(new Error("not deployed"));
  apiMock.notifications.mockResolvedValue({ notifications: [] });
  apiMock.me.mockResolvedValue({ user: { role: "Officer" } });
  apiMock.analytics.mockRejectedValue(new Error("not deployed"));
  apiMock.similar.mockResolvedValue({ duplicates: [], suggestedCategory: { category: null } });
  apiMock.listOfficers.mockResolvedValue({ source: "cognito", officers: [
    { sub: "officer-1", name: "Officer One", email: "o1@example.test", enabled: true },
    { sub: "officer-2", name: "Officer Two", email: "o2@example.test", enabled: true },
  ] });
});

afterEach(() => vi.useRealTimers());

// ---- authentication & role routing ------------------------------------
describe("role-based routing", () => {
  it("shows login when signed out", () => {
    authState.current = { user: null, session: null, role: null, loading: false, logout: vi.fn() };
    render(<App />);
    expect(screen.getByText("Login screen")).toBeInTheDocument();
  });

  it("shows a session check while loading", () => {
    authState.current = { loading: true };
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent(/Checking your CivicTrack session/);
  });

  it("blocks users without a CivicTrack group", () => {
    signIn("Unassigned");
    render(<App />);
    expect(screen.getByText("Access not assigned")).toBeInTheDocument();
    expect(apiMock.listComplaints).not.toHaveBeenCalled();
  });

  it.each([["Citizen", "Citizen Portal"], ["Officer", "Officer Portal"], ["Admin", "Administration"]])("routes %s to %s", async (role, portal) => {
    signIn(role);
    render(<App />);
    expect(await screen.findByText(new RegExp(`^${portal} /`), {}, { timeout: 5000 })).toBeInTheDocument();
  });
});

// ---- citizen -----------------------------------------------------------
describe("citizen portal", () => {
  beforeEach(() => signIn("Citizen"));

  it("renders stats from API data", async () => {
    render(<App />);
    const total = await screen.findByText("Total complaints");
    expect(total.parentElement).toHaveTextContent("5");
  });

  it("validates the complaint form before calling the API", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /report an issue/i }))[0]);
    await user.click(screen.getByRole("button", { name: /submit complaint/i }));
    expect(await screen.findByText(/give the issue a short title/i)).toBeInTheDocument();
    expect(screen.getByText(/describe the issue/i)).toBeInTheDocument();
    expect(apiMock.createComplaint).not.toHaveBeenCalled();
  });

  it("submits a valid complaint with an idempotency key", async () => {
    const user = userEvent.setup();
    apiMock.createComplaint.mockResolvedValue({ complaint: raw({ complaintId: "CT-NEWNEW0001", title: "Garbage pile" }) });
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /report an issue/i }))[0]);
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText(/issue title/i), "Garbage pile");
    await user.type(within(dialog).getByLabelText(/address/i), "Ward 80 market");
    await user.type(within(dialog).getByLabelText(/description/i), "Uncollected for 5 days");
    await user.click(within(dialog).getByRole("button", { name: /submit complaint/i }));
    await waitFor(() => expect(apiMock.createComplaint).toHaveBeenCalledTimes(1));
    const [, body] = apiMock.createComplaint.mock.calls[0];
    expect(body).toMatchObject({ title: "Garbage pile", location: "Ward 80 market", category: "Roads" });
    expect(body.clientRequestId).toMatch(/.{8,}/);
    expect(await screen.findByText(/tracking ID is CT-NEWNEW0001/)).toBeInTheDocument();
  });

  it("offers reopen/close only on resolved complaints and never admin actions", async () => {
    const user = userEvent.setup();
    apiMock.listComplaints.mockResolvedValue({ complaints: [raw({ complaintId: "CT-RESOLVED01", status: "RESOLVED", resolvedAt: new Date().toISOString(), title: "Fixed light" })] });
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /fixed light/i }));
    const drawer = screen.getByRole("dialog", { name: /fixed light/i });
    expect(within(drawer).getByRole("button", { name: /no, reopen/i })).toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: /assign/i })).not.toBeInTheDocument();
  });
});

// ---- officer -----------------------------------------------------------
describe("officer portal", () => {
  beforeEach(() => signIn("Officer"));

  it("shows overdue alert and only valid workflow actions", async () => {
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent(/breached the SLA/);
    expect(screen.getAllByRole("button", { name: /start work/i }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("button", { name: /resolve with proof/i }).length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: /^(re)?assign( officer)?$/i })).not.toBeInTheDocument();
  });

  it("requires a proof photo before resolving", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /resolve with proof/i }))[0]);
    await user.click(screen.getByRole("button", { name: /upload proof & resolve/i }));
    expect(await screen.findByText(/proof photograph is required/i)).toBeInTheDocument();
    expect(apiMock.resolve).not.toHaveBeenCalled();
  });

  it("starts work through the API", async () => {
    const user = userEvent.setup();
    apiMock.start.mockResolvedValue({ complaint: { ...SAMPLE[1], status: "IN_PROGRESS" } });
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /start work/i }))[0]);
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: /start work/i }));
    await waitFor(() => expect(apiMock.start).toHaveBeenCalledWith(expect.anything(), "CT-AAAAAAAAA2", {}));
  });
});

// ---- admin -------------------------------------------------------------
describe("admin dashboard", () => {
  beforeEach(() => signIn("Admin"));

  it("computes overview figures from records", async () => {
    render(<App />);
    const overdue = await screen.findByRole("button", { name: /overdue/i });
    expect(overdue).toHaveTextContent("1");
    expect(screen.getByRole("button", { name: /total complaints/i })).toHaveTextContent("5");
  });

  it("assigns a pending complaint to an officer picked from the directory", async () => {
    const user = userEvent.setup();
    apiMock.assign.mockResolvedValue({ message: "Complaint assigned successfully", complaint: { ...SAMPLE[0], status: "ASSIGNED", assignedOfficerSub: "officer-2", assignedOfficerName: "Officer Two" } });
    render(<App />);
    await user.click((await screen.findAllByRole("button", { name: /^assign$/i }))[0]);
    const dialog = screen.getByRole("dialog", { name: /assign complaint/i });
    await user.click(within(dialog).getByLabelText(/officer two/i));
    await user.click(within(dialog).getByRole("button", { name: /confirm assignment/i }));
    await waitFor(() => expect(apiMock.assign).toHaveBeenCalledWith(expect.anything(), "CT-AAAAAAAAA1", "officer-2", "", false));
    expect(await screen.findByText("Complaint assigned successfully")).toBeInTheDocument();
  });

  it("filters complaints and shows the officer workload", async () => {
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: /^complaints/i }));
    await user.selectOptions(screen.getByLabelText(/^status/i), "IN_PROGRESS");
    expect(screen.getByText("1 result")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^officers/i }));
    const row = screen.getByText("Officer One").closest("tr");
    expect(within(row).getAllByRole("cell")[4]).toHaveTextContent("2"); // Active column
  });
});
