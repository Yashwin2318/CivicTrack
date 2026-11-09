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
  assign: (auth, id, officerSub, reason) =>
    apiRequest(`/complaints/${enc(id)}/assign`, { auth, method: "PATCH", body: { officerSub, ...(reason ? { reason } : {}) } }),
  unassign: (auth, id, reason) => apiRequest(`/complaints/${enc(id)}/unassign`, { auth, method: "PATCH", body: reason ? { reason } : {} }),
  start: (auth, id, body = {}) => apiRequest(`/complaints/${enc(id)}/start`, { auth, method: "PATCH", body }),
  resolve: (auth, id, body) => apiRequest(`/complaints/${enc(id)}/resolve`, { auth, method: "PATCH", body }),
  reopen: (auth, id, reason) => apiRequest(`/complaints/${enc(id)}/reopen`, { auth, method: "PATCH", body: { reason } }),
  close: (auth, id, body = {}) => apiRequest(`/complaints/${enc(id)}/close`, { auth, method: "PATCH", body }),
  uploadOfficerPhoto: (auth, id, file, stage) =>
    uploadEvidence({ auth, file, uploadPath: `/complaints/${enc(id)}/proof-upload-url`, extraBody: { stage } }),
};

export function newRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

export { API_URL };
