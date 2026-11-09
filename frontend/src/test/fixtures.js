// Synthetic records for tests only. Times are relative to a fixed NOW.
export const NOW = new Date("2026-11-09T12:00:00Z").getTime();
const hoursAgo = (h) => new Date(NOW - h * 3600000).toISOString();

export function raw(overrides = {}) {
  return {
    complaintId: "CT-TEST000001",
    title: "Pothole near school",
    category: "Roads",
    location: "5th Cross, Indiranagar",
    description: "Large pothole",
    status: "PENDING",
    priority: "MEDIUM",
    createdAt: hoursAgo(2),
    slaHours: 48,
    citizenEmail: "citizen@example.test",
    assignedOfficerSub: "",
    assignedOfficerName: "",
    ...overrides,
  };
}

export const SAMPLE = [
  raw({ complaintId: "CT-AAAAAAAAA1", status: "PENDING", createdAt: hoursAgo(2), zone: "East", ward: "Ward 80", latitude: 12.9719, longitude: 77.6412 }),
  raw({ complaintId: "CT-AAAAAAAAA2", status: "PENDING", assignedOfficerSub: "officer-1", assignedOfficerName: "Officer One", createdAt: hoursAgo(45), category: "Streetlights", zone: "East", ward: "Ward 81" }),
  raw({ complaintId: "CT-AAAAAAAAA3", status: "IN_PROGRESS", assignedOfficerSub: "officer-1", assignedOfficerName: "Officer One", createdAt: hoursAgo(60), category: "Sanitation", zone: "West", latitude: 12.972, longitude: 77.6415 }),
  raw({ complaintId: "CT-AAAAAAAAA4", status: "RESOLVED", assignedOfficerSub: "officer-2", assignedOfficerName: "Officer Two", createdAt: hoursAgo(100), resolvedAt: hoursAgo(90), zone: "West" }),
  raw({ complaintId: "CT-AAAAAAAAA5", status: "CLOSED", assignedOfficerSub: "officer-2", assignedOfficerName: "Officer Two", createdAt: hoursAgo(200), resolvedAt: hoursAgo(140), reopenCount: 1 }),
];
