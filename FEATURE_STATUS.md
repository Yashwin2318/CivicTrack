# CivicTrack — Feature Status

Status as of 2026-11-09. **Local** = implemented and tested in the repository. **Live** = running in the deployed `civictrack-dev` stack. Nothing new is live until the owner approves a deployment (see `DEPLOYMENT_GUIDE.md`).

Legend: ✅ done · 🟡 partial / with limits · ⛔ not done (reason given)

## Citizen portal
| Feature | Local | Live | Notes |
|---|---|---|---|
| Registration, email confirmation, login | ✅ | ✅ | Unchanged Cognito flow (`Login.jsx`) |
| Profile page | ✅ | ⛔ | Email, account type, activity |
| Complaint creation with categories, description | ✅ | ✅ (basic) | Now with client-side validation mirroring backend |
| Location capture: GPS button + tap-on-map pin, landmark | ✅ | ⛔ | Coordinates validated server-side |
| Evidence photo upload (presigned S3 PUT) | ✅ | ✅ | Type/size checked in browser and by `HeadObject` |
| Duplicate-submit protection | ✅ | ⛔ | `clientRequestId` idempotency key |
| Complaint history, search, status/category/date filters | ✅ | 🟡 | Live has text search only |
| Tracking by ID | ✅ | 🟡 | |
| Details drawer with status timeline | ✅ | ⛔ | Legacy rows show a timeline rebuilt from timestamps |
| Resolution-proof and evidence viewing | ✅ | ⛔ | Needs `GET /complaints/{id}` + presigned GET |
| Email notification status | ✅ | ⛔ | `lastCitizenNotification` (sent / not delivered) |
| Reopen (with reason) / confirm & close (with rating) | ✅ | ⛔ | |
| Mobile layout | ✅ | 🟡 | Verified at 375 px, no horizontal scroll |

## Administrator dashboard
| Feature | Local | Live | Notes |
|---|---|---|---|
| Totals: pending, assigned/in progress, resolved, overdue, SLA warnings | ✅ | 🟡 | Live shows 4 basic counts |
| Assignment from officer directory (Cognito Officer group) | ✅ | ⛔ | Live requires pasting a Cognito `sub` |
| Reassign (before work starts) / unassign (ASSIGNED only) | ✅ | ⛔ | Confirmation dialog + reason recorded |
| Priority, department, zone, ward management per complaint | ✅ | ⛔ | Zones/wards are admin-entered text (no boundary data) |
| Evidence and proof preview | ✅ | ⛔ | |
| Complaint history | ✅ | ⛔ | Append-only `history` attribute |
| Filters: status, department, zone, ward, priority, SLA, date range | ✅ | 🟡 | |
| Officer workload, overdue, completion, avg time, SLA % | ✅ | ⛔ | |
| Department & zone → ward hierarchy statistics | ✅ | ⛔ | |
| Analytics: trends (day/week/month), category, department, zone, ward, officer, avg resolution time, SLA compliance, reopened, escalations, heatmap | ✅ | ⛔ | All from stored records |
| CSV export (records and summary) | ✅ | ⛔ | Formula-injection safe |
| Admin-only enforcement in backend | ✅ | ✅ | Every admin route re-checks role |
| Officer account creation / deactivation | ⛔ | ⛔ | Managed in Cognito console by design; CivicTrack never edits identities |
| Configurable department/zone catalogue | ⛔ | ⛔ | Needs a reference-data table — owner decision |

## Field officer portal
| Feature | Local | Live | Notes |
|---|---|---|---|
| Assigned complaints, details, profile | ✅ | 🟡 | |
| Start work with optional before-photo and note | ✅ | 🟡 | Live: start without photo/note |
| Resolve with mandatory after-photo + note | ✅ | 🟡 | Live: proof photo only |
| Directions link (Google Maps URL, no API key) | ✅ | ⛔ | |
| Field map + suggested visit order | ✅ | ⛔ | Straight-line nearest-next; **not** live tracking |
| SLA countdown, overdue banner, notification panel | ✅ | 🟡 | |
| Work history with avg resolution time and SLA % | ✅ | ⛔ | |
| Isolation from other officers' complaints | ✅ | ✅ | 403 on any other officer's complaint |

## Lifecycle, SLA, platform
| Feature | Local | Live | Notes |
|---|---|---|---|
| State machine PENDING→ASSIGNED→IN_PROGRESS→RESOLVED→(REOPENED|CLOSED) | ✅ | ⛔ | Backward compatible with live rows |
| Invalid transition prevention + optimistic concurrency | ✅ | 🟡 | Live has conditional writes for its 3 states |
| 48 h SLA, 4 h warning, 15-min schedule | ✅ | ✅ | |
| Exactly-once warning/escalation under retries & concurrency | ✅ | ⛔ | Live can abort a sweep or double-send (audit F1/F2) |
| Escalation history entry | ✅ | ⛔ | |
| Fresh SLA period after reopen | ✅ | ⛔ | |
| GIS: markers, status filter, clusters, density, nearby complaints | ✅ | ⛔ | OpenStreetMap tiles, free |
| Ward/zone boundary polygons | ⛔ | ⛔ | No verified boundary dataset supplied |
| Live vehicle/officer GPS tracking | ⛔ | ⛔ | No location source exists; not claimed |
| Automated tests | ✅ | — | 142 backend + 38 frontend, all passing |
| Local full-stack dev server (no Docker/SAM) | ✅ | — | `backend/tools/local_dev_server.py` |
| Infrastructure hardening in template | ✅ | ⛔ | Routes, IAM least privilege, S3 lifecycle |
| Log retention 30 days, PITR, GSIs, SES production access, CORS restriction | ⛔ | ⛔ | Owner approval needed (billing/deploy) |
| IEEE paper | ⛔ | — | Needs official template + real measurements from deployed system |
