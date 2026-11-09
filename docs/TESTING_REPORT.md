# CivicTrack Testing Report

Executed 2026-11-09 on Windows 11, Python 3.14.0, Node 24.16.0. All results below were produced by actually running the suites; nothing is extrapolated.

## Summary

| Suite | Tool | Tests | Result | Coverage |
|---|---|---|---|---|
| Backend unit + API + integration | pytest 9 + moto 5.2.5 | **142** | **142 passed** | 88% statements (`app.py` 87%, `lifecycle.py` 99%) |
| Frontend logic + components | Vitest 5 + Testing Library + jsdom | **38** | **38 passed** | 53% lines (logic libs high; map/leaflet code mocked) |
| Lint | ESLint 10 (react-hooks 7) | — | 0 problems | — |
| Production build | Vite 8 | — | success, per-role code split | — |
| IaC lint | cfn-lint | 2 templates | 0 findings | — |
| Dependency audit | `npm audit --omit=dev` | — | 0 vulnerabilities (1 moderate in dev-only `minimatch`) | — |

Commands:

```bash
backend/.venv/Scripts/python -m pytest backend/tests -q --cov=backend/complaints
```

```bash
cd frontend && npm test && npm run lint && npm run build
```

## Isolation from real data

Every backend test runs inside `moto.mock_aws()` with fake credentials and a table named `Test-CivicTrack-Complaints`; Cognito lookups are stubbed. **No test reads or writes the deployed table, bucket, user pool or SES identity.** SLA tests create synthetic records with fixed timestamps relative to `2026-11-09T12:00Z` — real complaints are never modified to simulate breaches.

## Backend coverage by area

| Area | File | Representative cases |
|---|---|---|
| Lifecycle rules | `test_lifecycle.py` (45; also `test_api.py` 56, `test_workflow.py` 20, `test_sla_and_notifications.py` 21) | legacy status mapping; full transition table incl. invalid actions; ISO parsing incl. naive and +05:30; SLA windows at 1/43.9/44/47.9/48/100 h; met/missed; reopen restarts clock |
| Authentication | `test_api.py` | 401 without claims; 403 without group; group claim as list / `[Admin]` / plain; Admin precedence; local demo headers ignored when deployed |
| Authorization & isolation | `test_api.py`, `test_workflow.py` | citizen sees only own list, 404 on others; officer sees only assigned, 403 otherwise; staff IDs hidden from citizens; admin-only directory/metadata/assign/unassign; officer cannot act on another officer's complaint |
| Validation | `test_api.py` | required fields, lengths, coordinates (pairing, range, bool, text), bad JSON, non-object body, complaint ID format, priority/department enums |
| Uploads (S3) | `test_api.py`, `test_workflow.py` | key prefix scoping, type allow-list, 10 MB limit, missing object, traversal and URL keys, proof key from another officer or a citizen rejected, proof URL only while IN_PROGRESS |
| Lifecycle integration | `test_workflow.py` | **end-to-end**: submit with photo → assign → start with before photo → proof → resolve → citizen notified → reopen → rework → resolve → close with rating, history verified; legacy row start; reassign/unassign; reopen rules; reassign after reopen |
| Concurrency & duplicates | `test_workflow.py`, `test_api.py` | stale read cannot overwrite a newer assignment (409); double start applies once; repeated `clientRequestId` returns same complaint |
| SLA automation | `test_sla_and_notifications.py` | scheduled event routing; no Cognito calls when nothing is due; warning exactly once; breach escalates once with history; competing claims → one winner; SES failure releases claim and next run retries; RESOLVED/CLOSED ignored; malformed/missing timestamps counted, sweep continues; naive timestamps as UTC; one failing record does not abort; reopened uses fresh SLA; legacy escalated-but-unnotified notified once; officer lookup cached |
| Notifications (SES) | `test_sla_and_notifications.py` | invalid addresses filtered and de-duplicated; rejection by SES returns False without raising; delivery status stored on complaint; email failure never blocks complaint creation; recipient addresses absent from logs |
| Error handling | `test_api.py` | unexpected exception → generic 500 with no detail |

## Frontend coverage

| Area | Cases |
|---|---|
| Lifecycle helpers | legacy mapping, transitions, UTC parsing, SLA windows, met/missed, normalisation, legacy timeline |
| Analytics | summary counts, resolution time, SLA compliance, group-by with "Unspecified", day trend gap-fill, filters (status/zone/search/date), CSV escaping and formula-injection guard |
| Geo | haversine, nearby, duplicates, clustering, visit order |
| Form validation | valid form, every invalid field, photo type and size, payload building with idempotency key |
| Role routing | signed out → login; loading; unassigned role blocked and no API call; Citizen/Officer/Admin → correct portal |
| Citizen | stats from data; validation blocks submission; valid submission sends payload with `clientRequestId` and shows tracking ID; reopen/close only on resolved; no admin actions |
| Officer | overdue banner; only valid workflow buttons; proof photo required; start calls API |
| Admin | overview figures; assignment via directory calls API with chosen officer; status filter; officer workload |

## Manual end-to-end verification (local stack)

Using `backend/tools/local_dev_server.py` (real Lambda code, AWS mocked by moto server) and the Vite dev server in a browser:

| Check | Result |
|---|---|
| Admin overview, map (OSM markers by status), analytics, detail drawer with mini-map, timeline, notification status | rendered correctly |
| Browser → presigned S3 PUT → backend `HeadObject` validation → complaint with view URL | PUT 200, complaint created, image URL loads |
| Citizen form at 375 px width: map pin, submit, toast with tracking ID | worked; coordinates stored (12.975459, 77.602844) |
| Another citizen requesting that complaint | 404 |
| Horizontal overflow at 1009 px and 375 px | none (two CSS defects found and fixed during this check) |

## Not covered / limitations

- **No test ran against the deployed AWS stack.** Cognito JWT validation by API Gateway, real SES delivery, real S3 CORS and IAM policies are verified only after deployment (smoke test in `DEPLOYMENT_GUIDE.md` §4).
- `get_s3_client` local-mode branch and some Cognito pagination paths are not exercised (stubbed).
- Leaflet map rendering is mocked in component tests and was checked manually.
- No load or performance testing was performed; scan-cost growth is modelled, not measured.
- No accessibility audit was performed (automated or manual). Form fields have labels and dialogs have ARIA roles in code, but keyboard navigation, screen-reader behaviour and colour contrast have not been tested.
- The SAM build/deploy commands in `DEPLOYMENT_GUIDE.md` have not been executed (SAM CLI not installed).
