# CivicTrack API Documentation

Base URL (dev stage): `https://4s044dr8u8.execute-api.ap-south-1.amazonaws.com/dev`

All routes except `GET /health` require `Authorization: Bearer <Cognito ID token>`. API Gateway validates the token; the Lambda derives the role **only** from the token's `cognito:groups` claim (precedence Admin > Officer > Citizen). Responses are JSON with `Cache-Control: no-store`.

**Errors** always have the shape `{"error": "<human message>", "code": "<MACHINE_CODE>"}`:

| HTTP | code | Meaning |
|---|---|---|
| 400 | `VALIDATION_ERROR` | invalid input (message names the field) |
| 401 | `UNAUTHENTICATED` | missing/invalid identity |
| 403 | `FORBIDDEN` | role not allowed, or officer accessing a complaint not assigned to them |
| 404 | `NOT_FOUND` | not found — also returned to citizens for other citizens' complaints (prevents ID probing) |
| 405 | `METHOD_NOT_ALLOWED` | |
| 409 | `CONFLICT` | invalid lifecycle transition, duplicate, or concurrent modification |
| 413 | `PAYLOAD_TOO_LARGE` | body > 1 MB (boundary imports excepted) |
| 429 | `RATE_LIMITED` | citizen complaint rate limit (API Gateway also throttles at 25 rps / burst 50) |
| 500 | `INTERNAL_ERROR` | generic; details only in logs |
| 501 | `NOT_CONFIGURED` | user administration without a Cognito pool (local only) |

`{id}` must match `CT-[A-Z0-9]{6,32}`.

## Endpoint summary

| Method & path | Roles | Purpose |
|---|---|---|
| `GET /health` | public | Liveness |
| `GET /me` | all | Caller identity; officers also get their profile |
| `GET /reference[?boundaries=1]` | all | Departments, categories, zones, wards (+GeoJSON), settings |
| `GET /notifications` | all | Activity feed from complaint history (30 days) |
| `POST /evidence/upload-url` | Citizen | Presigned PUT for complaint photo |
| `GET /complaints[?limit&nextToken]` | all | Complaints visible to caller (paginated when `limit` is set) |
| `POST /complaints` | Citizen | Create complaint |
| `POST /complaints/similar` | all | Duplicate check + category/priority/department/ward suggestions |
| `GET /complaints/{id}` | owner, assigned officer, Admin | Detail with fresh image URLs |
| `PATCH /complaints/{id}` | Admin | Priority, department, ward, zone |
| `PATCH /complaints/{id}/assign` | Admin | Assign or reassign (`force` overrides unavailability) |
| `PATCH /complaints/{id}/unassign` | Admin | ASSIGNED → PENDING |
| `PATCH /complaints/{id}/reject` | Admin | → REJECTED with reason (+ optional `duplicateOf`) |
| `PATCH /complaints/{id}/escalate` | Admin | Raise priority, escalation level +1, notify |
| `PATCH /complaints/{id}/accept` | assigned Officer | Accept assignment |
| `PATCH /complaints/{id}/decline` | assigned Officer | Decline with reason → PENDING |
| `POST /complaints/{id}/proof-upload-url` | assigned Officer | Presigned PUT; `stage` = before / progress / after |
| `PATCH /complaints/{id}/start` | assigned Officer | → IN_PROGRESS (optional `beforePhotoKey`, `note`) |
| `PATCH /complaints/{id}/progress` | assigned Officer | Note (required), `percentComplete`, optional `photoKey` |
| `PATCH /complaints/{id}/resolve` | assigned Officer | → RESOLVED with `proofPhotoKey` |
| `PATCH /complaints/{id}/reopen` | owner Citizen | RESOLVED → REOPENED (reason; ≤14 days; ≤3 times) |
| `PATCH /complaints/{id}/close` | owner Citizen, Admin | RESOLVED → CLOSED (`rating` 1–5, `feedback`) |
| `GET /officers` | Admin | Officer directory + profiles |
| `GET /analytics` | Admin | Server-side aggregation |
| `GET /admin/users?role=&nextToken=` | Admin | Cognito users by group |
| `POST /admin/users` | Admin | Invite Officer/Admin (Cognito emails temporary password) |
| `PATCH /admin/users/{username}` | Admin | Change `role` and/or `enabled` (not own account) |
| `PUT /admin/officers/{sub}/profile` | Admin | departmentId, zoneIds, wardIds, available, maxActive |
| `POST /admin/reference/initialize` | Admin | Persist built-in departments/categories |
| `PUT /admin/departments/{id}` | Admin | Create/update department |
| `PUT /admin/categories/{name}` | Admin | Category → department routing, visibility |
| `PUT /admin/zones/{id}`, `PUT /admin/wards/{id}` | Admin | Create/update area (+optional `boundary` geometry) |
| `POST /admin/boundaries/import` | Admin | Import GeoJSON FeatureCollection as zones or wards |
| `PUT /admin/settings` | Admin | slaHours, warningHours, maxComplaintsPerHour, discloseOfficerName |
| `GET /admin/audit?month=YYYY-MM` | Admin | Audit entries (newest first) |

## Selected payloads

### POST /complaints
| Field | Rules |
|---|---|
| `title`, `location`, `description` | required, ≤150 / ≤250 / ≤3000, no control characters |
| `category` | required; must be an active category from `/reference` |
| `latitude`, `longitude` | optional pair, range-checked, 6 dp |
| `locationAccuracy`, `landmark` (≤150) | optional |
| `citizenUrgency` | `LOW` / `MEDIUM` / `HIGH` |
| `photoKey` | key returned by `/evidence/upload-url`, verified with `HeadObject` |
| `clientRequestId` | `[A-Za-z0-9-]{8,64}`; repeat returns the same complaint with `duplicate: true` |

The server adds `department`, `departmentId`, `priority` (rule-based score), `summary`, `wardId/ward/zoneId/zone` (if boundaries contain the point), `suggestedCategory` (if keywords disagree) and `nearbyOpenCount`. `priorityScore`/`priorityReasons` are visible to staff only.

### POST /complaints/similar → 200
```json
{"suggestedCategory": {"category": "Roads", "confidence": 0.8, "matched": ["pothole"]},
 "department": {"id": "public-works", "name": "Public Works"},
 "priority": {"priority": "MEDIUM"},
 "ward": {"id": "w80", "name": "Ward 80"}, "zone": null,
 "duplicates": [{"distanceMeters": 40, "status": "PENDING", "category": "Roads", "createdAt": "…", "yours": false}],
 "summary": "…", "method": "rule-based keyword and distance matching (no AI service)"}
```
Citizens receive `complaintId`/`title` only for their own complaints; staff receive them for all.

### POST /admin/boundaries/import
```json
{"level": "ward", "featureCollection": {"type": "FeatureCollection", "features": [...]},
 "nameProperty": "WARD_NAME", "idProperty": "WARD_ID", "numberProperty": "WARD_NO", "zoneProperty": "ZONE", "source": "dataset + licence"}
```
Each feature must be a closed Polygon/MultiPolygon in `[longitude, latitude]`, ≤20 000 vertices and ≤300 KB serialized. Response: `{"imported": [...], "skipped": [{"index", "name", "reason"}]}` — invalid features are skipped, never partially stored. ≤500 features per call.

### GET /analytics
Query: `from`, `to` (YYYY-MM-DD, UTC), `department`, `zone`, `ward`, `category`, `priority` (names), `granularity` = day/week/month.
Response: `summary` (total, open, resolved, rejected, overdue, warning, newInPeriod, resolutionRate, avgResolutionHours, medianResolutionHours, slaComplianceRate, slaMet, slaEvaluated, reopenedEver, escalated, byStatus, bySla), `byDepartment`, `byCategory`, `byPriority`, `byZone`, `byWard`, `byOfficer` (each row: total, open, resolved, overdue, resolutionRate, avgResolutionHours, slaComplianceRate), `trend` [{period, created, resolved}], `hotspots` [{latitude, longitude, count, topCategory}], `mapped`.

### Complaint object (response)
Stored attributes (see `DATABASE_SCHEMA.md`) plus `effectiveStatus`, `department`, `priority`, `sla {state, deadline, warningAt, hours}`, and short-lived (300 s) `photoUrl`, `beforePhotoUrl`, `progressPhotoUrls[]`, `proofPhotoUrl`. `sla.state` ∈ `unknown | on_track | warning | breached | met | missed | not_applicable`. Citizens never receive staff subject IDs, triage internals or SLA bookkeeping flags; officer names are withheld when `discloseOfficerName` is false.

## Scheduled event
EventBridge `{"source": "aws.events"}` → SLA sweep. Returns `{scanned, warningsSent, newEscalations, secondLevelEscalations, notificationEmailsSent, malformedTimestamps, errors, checkedAt, warningHours}`.
