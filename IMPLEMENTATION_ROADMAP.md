# CivicTrack — Implementation Roadmap

## Status (2026-11-10)

| Phase | Status |
|---|---|
| 1. Backend correctness & security | ✅ done (commit `a35eb60`) |
| 2. Backend test suite | ✅ done; extended to 221 tests |
| 3. Frontend portals | ✅ done; extended in the platform-completion iteration |
| 4. Frontend tests | ✅ done; 65 tests |
| 5. Infrastructure (template) | ✅ written, ⛔ **not deployed** (owner approval) |
| 6. Documentation | ✅ done |
| 7. Platform completion: reference data, user admin, officer responses, reject/escalate, GIS boundaries, server analytics, rule-based intelligence, notification centre, password reset, rate limiting | ✅ done locally (uncommitted at time of writing) |
| 8. Deployment, boundary data, SES production access, log retention, PITR | ⛔ pending owner decisions |

### Next phases (proposed)

| Phase | Work | Needs |
|---|---|---|
| 9 | Deploy and smoke-test on AWS (`DEPLOYMENT_GUIDE.md` §3–4) | approval |
| 10 | Import official ward/zone GeoJSON with licence | dataset from owner |
| 11 | Scale work: UI pagination for admin lists, sparse GSI for open complaints, officer GSI after normalising empty officer IDs, daily analytics roll-ups, per-user notification items | approval (schema/cost) |
| 12 | Accessibility audit, load test on AWS, real AWS measurements for the paper | deployed environment |

The original plan follows.

Ordering principle: fix correctness/security of what is already deployed first, then complete the MVP, then extend. Items marked **[needs deploy]** require `sam deploy`, which is never run without explicit owner approval.

## Phase 1 — Backend correctness and security (local code)
1. Reformat `app.py` (remove blank-line padding) without behaviour change.
2. Extract pure lifecycle module (`lifecycle.py`): statuses, transitions, legacy mapping, SLA math.
3. SLA sweep: claim-then-send idempotency (F1, F2), lazy + cached officer lookup (F3), skip `CLOSED`, count malformed timestamps, escalation history.
4. Audit `history` on every action.
5. New endpoints **[needs deploy for API Gateway routes]**:
   - `GET /complaints/{id}` — detail + presigned view URLs (fixes "evidence cannot be viewed").
   - `GET /officers` — Admin officer directory with workload (replaces pasting a Cognito sub).
   - `PATCH /complaints/{id}` — Admin: priority, zone, ward, department.
   - `PATCH /complaints/{id}/assign` — now also supports reassignment before work starts.
   - `PATCH /complaints/{id}/unassign` — Admin, only `ASSIGNED`.
   - `PATCH /complaints/{id}/reopen` — Citizen owner, `RESOLVED` only, reason required.
   - `PATCH /complaints/{id}/close` — Citizen owner or Admin, `RESOLVED` only.
   - `PATCH /complaints/{id}/start` and `/resolve` — accept `ASSIGNED`/`REOPENED`; optional officer note.
6. Optional coordinates (`latitude`, `longitude`) on create, validated to range.

## Phase 2 — Backend test suite
pytest + moto (DynamoDB, S3, SES mocked; Cognito lookups stubbed). Covers routing, authN, authZ isolation, validation, lifecycle transitions, concurrency conditions, uploads, SLA idempotency with controlled in-memory records only.

## Phase 3 — Frontend
1. Shared layer: status/lifecycle helpers mirroring the backend, toast + confirm dialog, complaint detail drawer with timeline and evidence/proof images, Leaflet map (OpenStreetMap tiles).
2. Citizen: location capture (GPS button / click-on-map), filters, detail view, reopen/close, mobile layout.
3. Admin: officer picker, reassign/unassign with confirmation, priority/zone/ward editing, filters (status, department, zone, ward, date range), SLA indicators, map with density layer, analytics (trends, distribution, department/ward/zone/officer performance, avg resolution time, SLA compliance, reopened count), CSV export.
4. Officer: detail with navigation link, start/resolve with notes, SLA countdown and overdue alerts, work history.

## Phase 4 — Frontend tests
Vitest + Testing Library: helpers, form validation, role routing, dashboards rendering from mocked API data.

## Phase 5 — Infrastructure (template only) **[needs deploy]**
- Add new API routes.
- CloudWatch log retention 30 days (F6).
- S3 lifecycle: abort incomplete multipart uploads (F12). Evidence retention policy deferred to owner decision.
- Remove unused `dynamodb:DeleteItem` (F9).
- Exclude dead files from the Lambda package (F10) — files deleted from the code folder after the baseline snapshot.
- Proposals needing owner decision (billing): PITR (F7), GSIs on `citizenSub` / `assignedOfficerSub` (F4), SES production access (F8), restricting CORS once the frontend is hosted (F5).

## Phase 6 — Documentation
Resource inventory, budget audit, cost model workbook, deployment guide, API reference, schema, security architecture, UML set, testing report, user manual.

## Deferred / out of scope without further input
- Ward/zone **boundary polygons**: needs a verified official dataset (e.g. BBMP GeoJSON) supplied by the owner.
- Officer **route optimisation / live tracking**: architecture notes only; no GPS source exists.
- **IEEE paper**: requires the official IEEE template and real experimental measurements from the deployed system; not fabricated here.
- Reference-data table for configurable departments/zones (new DynamoDB table) — owner decision.
