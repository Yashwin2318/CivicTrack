# CivicTrack API Reference

Base URL (dev stage): `https://4s044dr8u8.execute-api.ap-south-1.amazonaws.com/dev`

All routes except `/health` require `Authorization: Bearer <Cognito ID token>`. API Gateway validates the token; the Lambda derives the role from `cognito:groups` (precedence Admin > Officer > Citizen). Responses are JSON with `Cache-Control: no-store`. Errors are `{"error": "<message>"}` and never include stack traces.

Routes marked **NEW** exist in code and template but are not deployed yet.

| Method & path | Roles | Purpose |
|---|---|---|
| `GET /health` | public | Liveness |
| `POST /evidence/upload-url` | Citizen | Presigned PUT for complaint photo |
| `GET /complaints` | all | List complaints visible to caller |
| `POST /complaints` | Citizen | Create complaint |
| `GET /complaints/{id}` **NEW** | owner Citizen, assigned Officer, Admin | Detail with fresh image URLs |
| `PATCH /complaints/{id}` **NEW** | Admin | Priority / department / zone / ward |
| `GET /officers` **NEW** | Admin | Officer directory (Cognito `Officer` group) |
| `PATCH /complaints/{id}/assign` | Admin | Assign or reassign |
| `PATCH /complaints/{id}/unassign` **NEW** | Admin | Remove officer (ASSIGNED only) |
| `POST /complaints/{id}/proof-upload-url` | assigned Officer | Presigned PUT for before/after photo |
| `PATCH /complaints/{id}/start` | assigned Officer | Start work |
| `PATCH /complaints/{id}/resolve` | assigned Officer | Resolve with proof |
| `PATCH /complaints/{id}/reopen` **NEW** | owner Citizen | Reopen a resolved complaint |
| `PATCH /complaints/{id}/close` **NEW** | owner Citizen, Admin | Close a resolved complaint |

`{id}` must match `CT-[A-Z0-9]{6,32}` (400 otherwise).

## Status codes

| Code | Meaning |
|---|---|
| 200 / 201 | Success / created |
| 400 | Validation error (message says which field) |
| 401 | Missing/invalid identity |
| 403 | Role not allowed, or officer accessing a complaint not assigned to them |
| 404 | Not found — also returned to a citizen asking for another citizen's complaint (prevents ID probing) |
| 405 | Method not allowed on route |
| 409 | Invalid lifecycle transition, or concurrent modification ("refresh and retry") |
| 500 | Generic internal error |

## Complaint object (response)

Stored attributes (see `DATABASE_SCHEMA.md`) plus derived fields:

```json
{
  "complaintId": "CT-1A2B3C4D5E",
  "status": "PENDING",
  "effectiveStatus": "ASSIGNED",
  "department": "Public Works",
  "priority": "MEDIUM",
  "sla": {"state": "warning", "deadline": "2026-11-11T09:00:00+00:00", "warningAt": "2026-11-11T05:00:00+00:00", "hours": 48},
  "photoUrl": "https://…X-Amz-Expires=300…",
  "proofPhotoUrl": "https://…",
  "history": [{"at": "…", "action": "created", "actorRole": "Citizen", "fromStatus": null, "toStatus": "PENDING"}]
}
```

`sla.state` ∈ `unknown | on_track | warning | breached | met | missed`. Image URLs expire after 300 s. For citizens, `assignedOfficerSub`, `assignedBySub`, `resolvedBySub` and history `actorSub` are removed.

## Endpoint details

### POST /evidence/upload-url
Body `{"contentType": "image/jpeg|image/png|image/webp", "fileSize": 123456}` (`fileSize` optional, ≤ 10 MB).
Response `{"uploadUrl", "photoKey", "expiresIn": 300, "requiredHeaders": {"Content-Type": …}}`. PUT the file to `uploadUrl` with that header, then send `photoKey` when creating the complaint. The server re-checks the object's type and size with `HeadObject`.

### POST /complaints
| Field | Rules |
|---|---|
| `title` | required, ≤150 |
| `category` | required, ≤60 (UI: Roads, Sanitation, Streetlights, Waterlogging, Water Supply, Other) |
| `location` | required, ≤250 |
| `description` | required, ≤3000 |
| `latitude`, `longitude` | optional, both or neither, valid ranges, stored to 6 dp |
| `locationAccuracy` | optional metres |
| `landmark` | optional, ≤150 |
| `photoKey` | optional, must be under `citizens/{caller sub}/complaint-evidence/` and uploaded |
| `clientRequestId` | optional `[A-Za-z0-9-]{8,64}`; repeating it returns the same complaint with `200` and `"duplicate": true` |

Returns `201 {"message", "complaint"}`. Citizen identity and email come from the token, never the body.

### GET /complaints
Citizen → own; Officer → assigned to them; Admin → all. Sorted newest first. Response `{"count", "complaints", "serverTime"}`.

### PATCH /complaints/{id} (Admin)
Any of `priority` (LOW|MEDIUM|HIGH|CRITICAL), `department` (one of the six departments), `zone`, `ward` (≤80, empty string clears). Appends a `details_updated` history entry.

### GET /officers (Admin)
`{"officers": [{"sub", "email", "name", "enabled", "status"}], "source": "cognito"}`.

### PATCH /complaints/{id}/assign (Admin)
Body `{"officerSub": "...", "reason": "optional"}`. The officer must be an **enabled member of the Cognito Officer group**; the stored name comes from Cognito. Allowed from PENDING or REOPENED (assign) and ASSIGNED (reassign to a different officer). 409 once work has started.

### PATCH /complaints/{id}/unassign (Admin)
Body `{"reason": "optional"}`. ASSIGNED → PENDING.

### POST /complaints/{id}/proof-upload-url (assigned Officer)
Body `{"contentType": …, "stage": "before" | "after"}` (default `after`). `after` requires IN_PROGRESS; `before` allowed while ASSIGNED, REOPENED or IN_PROGRESS. Response includes `proofPhotoKey`.

### PATCH /complaints/{id}/start (assigned Officer)
Body optional `{"note": "...", "beforePhotoKey": "officers/{sub}/complaints/{id}/before/…"}`. ASSIGNED/REOPENED → IN_PROGRESS.

### PATCH /complaints/{id}/resolve (assigned Officer)
Body `{"proofPhotoKey": "officers/{sub}/complaints/{id}/proof/…", "note": "optional"}`. IN_PROGRESS → RESOLVED. Citizen and admin are emailed.

### PATCH /complaints/{id}/reopen (owner Citizen)
Body `{"reason": "≥5 characters"}`. RESOLVED → REOPENED, starts a fresh SLA period, clears SLA notification flags, increments `reopenCount`, emails admin and officer.

### PATCH /complaints/{id}/close (owner Citizen or Admin)
Body `{"note": "optional", "rating": 1-5 (citizen only)}`. RESOLVED → CLOSED.

## Scheduled event
EventBridge `{"source": "aws.events", "detail-type": "Scheduled Event"}` → SLA sweep. Returns `{"scanned", "warningsSent", "newEscalations", "notificationEmailsSent", "malformedTimestamps", "errors", "checkedAt"}`.
