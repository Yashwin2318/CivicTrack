const API_URL = (import.meta.env.VITE_API_URL || "http://127.0.0.1:3000").replace(/\/$/, "");
const DEMO_AUTH_ENABLED =
  import.meta.env.DEV && import.meta.env.VITE_ENABLE_DEMO_AUTH === "true";

function getDemoHeaders(auth) {
  if (!DEMO_AUTH_ENABLED || !auth?.isDemo || !auth?.user || !auth?.role) {
    throw new Error("Local demo authentication is disabled or incomplete.");
  }

  return {
    "X-Local-Demo-Role": auth.role,
    "X-Local-Demo-Sub": auth.user.userId || `local-demo-${auth.role.toLowerCase()}`,
    "X-Local-Demo-Email": auth.user.username || `demo.${auth.role.toLowerCase()}@civictrack.local`,
  };
}

export function getApiHeaders(auth, { json = false } = {}) {
  const headers = {};

  if (json) headers["Content-Type"] = "application/json";

  if (auth?.isDemo) {
    Object.assign(headers, getDemoHeaders(auth));
    return headers;
  }

  const idToken = auth?.session?.tokens?.idToken;
  if (!idToken) {
    throw new Error("Your login session has no Cognito ID token. Please sign in again.");
  }

  headers.Authorization = `Bearer ${idToken.toString()}`;
  return headers;
}

async function readResponse(response) {
  const text = await response.text();
  let data = {};

  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { message: text };
    }
  }

  if (!response.ok) {
    throw new Error(
      data.error || data.message || `Request failed (${response.status}).`
    );
  }

  return data;
}

export async function apiRequest(
  path,
  { auth, method = "GET", body, headers: extraHeaders = {} } = {}
) {
  const headers = {
    ...getApiHeaders(auth, { json: body !== undefined }),
    ...extraHeaders,
  };

  const response = await fetch(`${API_URL}${path}`, {
    method,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  return readResponse(response);
}

export async function uploadEvidence({ auth, file, uploadPath, extraBody = {} }) {
  if (!(file instanceof File)) {
    throw new Error("Choose an image before uploading.");
  }

  const allowedTypes = ["image/jpeg", "image/png", "image/webp"];
  if (!allowedTypes.includes(file.type)) {
    throw new Error("Use a JPG, PNG or WEBP image.");
  }

  if (file.size > 10 * 1024 * 1024) {
    throw new Error("The image must be 10 MB or smaller.");
  }

  const signed = await apiRequest(uploadPath, {
    auth,
    method: "POST",
    body: { contentType: file.type, fileSize: file.size, ...extraBody },
  });

  const objectKey = signed.photoKey || signed.proofPhotoKey;
  if (!signed.uploadUrl || !objectKey) {
    throw new Error("The server did not return a valid upload URL.");
  }

  const uploadResponse = await fetch(signed.uploadUrl, {
    method: "PUT",
    headers: {
      "Content-Type": file.type,
      ...(signed.requiredHeaders || {}),
    },
    body: file,
  });

  if (!uploadResponse.ok) {
    throw new Error(`Image upload failed (${uploadResponse.status}).`);
  }

  return objectKey;
}

export function validateImageFile(file) {
  if (!file) return "Choose an image.";
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) return "Use a JPG, PNG or WEBP image.";
  if (file.size > 10 * 1024 * 1024) return "The image must be 10 MB or smaller.";
  if (file.size === 0) return "The selected file is empty.";
  return "";
}

const enc = encodeURIComponent;

export const api = {
  listComplaints: (auth) => apiRequest("/complaints", { auth }),
  getComplaint: (auth, id) => apiRequest(`/complaints/${enc(id)}`, { auth }),
  createComplaint: (auth, body) => apiRequest("/complaints", { auth, method: "POST", body }),
  updateDetails: (auth, id, body) => apiRequest(`/complaints/${enc(id)}`, { auth, method: "PATCH", body }),
  listOfficers: (auth) => apiRequest("/officers", { auth }),
  assign: (auth, id, officerSub, reason, force = false) =>
    apiRequest(`/complaints/${enc(id)}/assign`, { auth, method: "PATCH", body: { officerSub, ...(reason ? { reason } : {}), ...(force ? { force: true } : {}) } }),
  unassign: (auth, id, reason) => apiRequest(`/complaints/${enc(id)}/unassign`, { auth, method: "PATCH", body: reason ? { reason } : {} }),
  start: (auth, id, body = {}) => apiRequest(`/complaints/${enc(id)}/start`, { auth, method: "PATCH", body }),
  resolve: (auth, id, body) => apiRequest(`/complaints/${enc(id)}/resolve`, { auth, method: "PATCH", body }),
  reopen: (auth, id, reason) => apiRequest(`/complaints/${enc(id)}/reopen`, { auth, method: "PATCH", body: { reason } }),
  close: (auth, id, body = {}) => apiRequest(`/complaints/${enc(id)}/close`, { auth, method: "PATCH", body }),
  uploadOfficerPhoto: (auth, id, file, stage) =>
    uploadEvidence({ auth, file, uploadPath: `/complaints/${enc(id)}/proof-upload-url`, extraBody: { stage } }),
  // Officer responses and progress
  accept: (auth, id) => apiRequest(`/complaints/${enc(id)}/accept`, { auth, method: "PATCH", body: {} }),
  decline: (auth, id, reason) => apiRequest(`/complaints/${enc(id)}/decline`, { auth, method: "PATCH", body: { reason } }),
  progress: (auth, id, body) => apiRequest(`/complaints/${enc(id)}/progress`, { auth, method: "PATCH", body }),
  // Admin decisions
  reject: (auth, id, reason, duplicateOf) =>
    apiRequest(`/complaints/${enc(id)}/reject`, { auth, method: "PATCH", body: { reason, ...(duplicateOf ? { duplicateOf } : {}) } }),
  escalate: (auth, id, reason) => apiRequest(`/complaints/${enc(id)}/escalate`, { auth, method: "PATCH", body: { reason } }),
  // Intelligence, notifications, profile, analytics
  similar: (auth, body) => apiRequest("/complaints/similar", { auth, method: "POST", body }),
  notifications: (auth) => apiRequest("/notifications", { auth }),
  me: (auth) => apiRequest("/me", { auth }),
  analytics: (auth, params) => apiRequest(`/analytics${toQuery(params)}`, { auth }),
  // Reference data and administration
  reference: (auth, withBoundaries = false) => apiRequest(`/reference${withBoundaries ? "?boundaries=1" : ""}`, { auth }),
  initializeReference: (auth) => apiRequest("/admin/reference/initialize", { auth, method: "POST", body: {} }),
  saveDepartment: (auth, id, body) => apiRequest(`/admin/departments/${enc(id)}`, { auth, method: "PUT", body }),
  saveCategory: (auth, name, body) => apiRequest(`/admin/categories/${enc(name)}`, { auth, method: "PUT", body }),
  saveZone: (auth, id, body) => apiRequest(`/admin/zones/${enc(id)}`, { auth, method: "PUT", body }),
  saveWard: (auth, id, body) => apiRequest(`/admin/wards/${enc(id)}`, { auth, method: "PUT", body }),
  importBoundaries: (auth, body) => apiRequest("/admin/boundaries/import", { auth, method: "POST", body }),
  saveSettings: (auth, body) => apiRequest("/admin/settings", { auth, method: "PUT", body }),
  auditLog: (auth, month) => apiRequest(`/admin/audit${toQuery({ month })}`, { auth }),
  saveOfficerProfile: (auth, sub, body) => apiRequest(`/admin/officers/${enc(sub)}/profile`, { auth, method: "PUT", body }),
  listUsers: (auth, role, nextToken) => apiRequest(`/admin/users${toQuery({ role, nextToken })}`, { auth }),
  createUser: (auth, body) => apiRequest("/admin/users", { auth, method: "POST", body }),
  updateUser: (auth, username, body) => apiRequest(`/admin/users/${enc(username)}`, { auth, method: "PATCH", body }),
};

export function toQuery(params = {}) {
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== "" && v !== "ALL");
  return entries.length ? `?${new URLSearchParams(entries).toString()}` : "";
}

export function slugify(name) {
  return (name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 48);
}

export function newRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export { API_URL };
