# CivicTrack — Project Audit

Audit date: 2026-11-09. Scope: every source file in `backend/`, `frontend/src/`, `infrastructure/`, `events/`, plus **read-only** inspection of the deployed `civictrack-dev` stack in `ap-south-1`. No AWS resource was modified during the audit.

## 1. What was inspected

| Area | Files / resources |
|---|---|
| Backend | `backend/complaints/app.py` (single Lambda, 3105 lines incl. blank-line padding), `backend/post_confirmation/app.py` |
| Frontend | `App.jsx` (citizen portal), `admin/AdminDashboard.jsx`, `officer/OfficerDashboard.jsx`, `auth/*`, `lib/api.js` |
| IaC | `infrastructure/template.yaml` (deployed), `template.local.yaml` (SAM local), `samconfig.toml` |
| Deployed stack | `aws cloudformation get-template` (deployed template is semantically identical to local `template.yaml`; only YAML formatting differs) |
| Deployed code | `.aws-sam/build/ComplaintsFunction/app.py` is byte-identical to `backend/complaints/app.py` |
| Live data | Projection-only scan (status + flags only, no PII printed): **6 complaints** — 1 `PENDING` unassigned, 1 `PENDING` assigned, 4 `RESOLVED`; 2 escalated, 2 SLA-warned; none have coordinates or ward |

## 2. Implemented and working (pre-audit)

- Cognito sign-up / confirm / sign-in / forced new-password flow (Amplify v6, email login).
- Post-confirmation trigger adds self-registered users to the `Citizen` group.
- API Gateway REST API with Cognito authorizer on every route except `/health`.
- Role resolution from `cognito:groups` in the **backend** (Admin > Officer > Citizen).
- Citizen: create complaint (title/category/location text/description), optional photo via presigned S3 PUT, list own complaints, search.
- Admin: list all complaints, assign to an officer (officer sub verified against Cognito `Officer` group; conditional write prevents double assignment).
- Officer: list own assigned complaints, start work (`PENDING → IN_PROGRESS`), upload proof + resolve (`IN_PROGRESS → RESOLVED`), both with conditional writes.
- Upload validation: content-type allow-list, `HeadObject` check of size (≤10 MB) and type, key-prefix scoping per user.
- SES notifications on create / assign / start / resolve / SLA warning / SLA breach.
- EventBridge `rate(15 minutes)` SLA sweep with one-time warning and escalation flags.
- Private, encrypted, public-access-blocked S3 bucket; SSE on DynamoDB.

## 3. Partially implemented

| Feature | Gap |
|---|---|
| Evidence viewing | Photos are uploaded but **no endpoint returns a viewable URL**; nobody (citizen, admin, officer) can see evidence or proof. |
| Officer assignment UX | Admin must **paste an officer's Cognito `sub`** by hand; there is no officer directory endpoint. |
| Officer / Department / Analytics tabs | Derived only from complaint records; department = complaint category; analytics = one bar list. |
| Status model | `PENDING` means both "unassigned" and "assigned, not started"; no `ASSIGNED`, `REOPENED`, `CLOSED`. `RESOLVED` is terminal. |
| Location | Free text only. `leaflet`/`react-leaflet` are installed but never imported. |
| Notification bell / Settings buttons | Rendered but do nothing. |
| `ProtectedRoute.jsx` | Not used (routing is role-switch inside `App.jsx`). |

## 4. Missing

Complaint detail view, status timeline, audit history, unassign/reassign, priority/ward/zone management, reopen/close, map, analytics (trends, resolution time, SLA compliance), CSV export, automated tests (backend and frontend), toasts/confirm dialogs, technical documentation.

## 5. Defects and security findings

Severity: **H** high, **M** medium, **L** low.

| # | Sev | Finding | Evidence |
|---|---|---|---|
| F1 | H | **SLA sweep can abort mid-run.** After a successful email, the flag write uses a `ConditionExpression`; a concurrent run (EventBridge at-least-once delivery, Lambda retry) makes it raise `ConditionalCheckFailedException`, which is not caught → the whole sweep raises and remaining complaints are skipped that cycle. | `app.py` lines ~1013 and ~1031 |
| F2 | H | **Duplicate SLA emails under concurrency.** Check-then-send pattern: two overlapping invocations both read "not sent" and both send before either writes the flag. | same |
| F3 | M | **SLA sweep calls Cognito for every unresolved complaint every 15 min**, paginating the entire Officer group each time, even when no notification is due. Cost/latency grows O(complaints × officers); risks Cognito throttling. | `process_sla_checks` line ~974 |
| F4 | M | `GET /complaints` does a full-table `Scan` with `ConsistentRead` for **every** role; citizen/officer filtering happens after the read, so each request consumes RCUs for the whole table. Fine at 6 rows, scales poorly. A GSI fix needs deployment (see roadmap). | `get_complaints` |
| F5 | M | CORS `AllowedOrigin='*'` is the deployed value (`samconfig.toml`). Bearer tokens are not cookies, so this is not a credential leak, but the S3 bucket CORS and API should be restricted to the real frontend origin once hosted. | `samconfig.toml` |
| F6 | M | CloudWatch log groups have **no retention** (never expire) and logs contain recipient email addresses (`EMAIL SENT TO:`). PII kept indefinitely. | `describe-log-groups` → `retentionInDays: None` |
| F7 | M | DynamoDB point-in-time recovery **disabled** on the only data table. | `template.yaml` |
| F8 | M | SES account is in **sandbox** (`ProductionAccessEnabled: False`, 200/day): citizen emails are only delivered to verified addresses. | `sesv2 get-account` |
| F9 | L | IAM grants `dynamodb:DeleteItem` that no code path uses (least privilege). | `template.yaml` |
| F10 | L | Lambda package ships dead files (`app_backup.py`, `app-source.txt`, `*_test.py`) because `CodeUri` is the whole folder. | `.aws-sam/build/ComplaintsFunction/` |
| F11 | L | `ALLOW_USER_PASSWORD_AUTH` enabled on the app client although Amplify uses SRP. | `template.yaml` |
| F12 | L | Evidence bucket has no lifecycle rule; orphan uploads (URL issued, complaint never submitted) persist forever. | `get-bucket-lifecycle-configuration` → none |
| F13 | L | Repository has **no commits**; there is no baseline to diff or roll back against. | `git log` |
| F14 | L | `app.py` is padded with 2–3 blank lines between almost every line, making review error-prone. | — |

Not a finding (verified OK): authorization is enforced server-side for every action; citizen and officer data isolation is enforced in the scan filter and on every single-item action; S3 keys are prefix-scoped per user; presigned PUT URLs expire in 300 s; no AWS credentials in frontend code (`.env.production` holds only public pool/client IDs and the API URL); `events/local-env.json` contains only dummy SeaweedFS credentials for local use.

## 6. Data model (as found)

Single table `CivicTrack-Complaints`, PK `complaintId` (`CT-XXXXXXXXXX`), on-demand billing, no GSIs.
Attributes: `title, category, location, description, photoKey, citizenSub, citizenEmail, assignedOfficerSub, assignedOfficerName, assignedAt, assignedBySub, status, priority, createdAt, slaHours, escalated, escalatedAt, slaWarningSentAt, escalationNotifiedAt, workStartedAt, resolvedAt, resolvedBySub, proofPhotoKey`.

## 7. Decisions taken from this audit

1. **Status compatibility**: keep stored values `PENDING`, `IN_PROGRESS`, `RESOLVED`; add `ASSIGNED`, `REOPENED`, `CLOSED`. Legacy `PENDING` + officer is read as `ASSIGNED`. No data migration is required; existing rows stay valid. `SUBMITTED` is not introduced as a separate stored state — `PENDING` already means "submitted, awaiting triage".
2. **Departments** are derived from a fixed category → department map in code (these are CivicTrack's own categories, not geography).
3. **Zones and wards** are admin-entered free-text fields on each complaint. No boundary polygons are hardcoded, because no verified boundary dataset was supplied.
4. All infrastructure changes are written into `template.yaml` but **not deployed** (see `DEPLOYMENT_GUIDE.md`).
