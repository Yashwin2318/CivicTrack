# CivicTrack Testing Report

Executed 2026-11-09 on Windows 11 (Intel x64, 8 logical CPUs), Python 3.14.0, Node 24.16.0. Every number below comes from an actual run; nothing is extrapolated.

## Summary

| Suite | Tool | Tests | Result | Coverage |
|---|---|---|---|---|
| Backend unit, API, authorization, lifecycle, DynamoDB, S3, SES, Cognito, SLA, failure handling, end-to-end | pytest 9 + moto 5.2.5 | **221** | **221 passed** | **92%** statements (`app.py` 91%, `intelligence.py` 100%, `lifecycle.py` 97%, `geo.py` 95%, `analytics_engine.py` 95%, `reference.py` 91%) |
| Frontend logic, components, forms, auth flows, navigation, role visibility, API integration (mocked) | Vitest 5 + Testing Library + jsdom | **65** | **65 passed** | 62.7% lines |
| Template ↔ router consistency | pytest + cfn-lint decoder | 2 | passed | both templates |
| Static analysis | pyflakes, ESLint 10 (react-hooks 7), cfn-lint | — | 0 findings | — |
| Production build | Vite 8 | — | success | per-role code split |
| Dependency audit | `npm audit --omit=dev` | — | 0 vulnerabilities | — |

```bash
backend/.venv/Scripts/python -m pytest backend/tests -q --cov=backend/complaints
```

```bash
cd frontend && npm test && npm run lint && npm run build
```

## Isolation from real data

All backend tests run inside `moto.mock_aws()` with fake credentials and test-only table, bucket and pool names. No test touches the deployed table, bucket, user pool or SES identity. SLA tests use synthetic records with fixed timestamps; real complaints are never modified to simulate breaches.

## Backend suites

| File | Tests | Covers |
|---|---|---|
| `test_lifecycle.py` | 45 | statuses incl. legacy mapping, transition table, UTC parsing, SLA windows, met/missed |
| `test_api.py` | 56 | authN (missing claims, no group, claim formats), routing, CORS, demo-header rejection, create validation, idempotency, uploads, isolation, officer directory, metadata, generic errors |
| `test_workflow.py` | 20 | full lifecycle, invalid transitions, officer isolation, proof prefix, assignment rules, unassign, reopen, concurrency guard, double start |
| `test_sla_and_notifications.py` | 21 | sweep routing, exactly-once warning/escalation, duplicate delivery, retry after SES failure, malformed timestamps, per-record isolation, reopen, legacy rows, lookup caching, SES filtering/rejection, notification status, PII-free logs |
| `test_intelligence_geo.py` | 16 | category suggestion, priority scoring, summary, point-in-polygon (holes, MultiPolygon), GeoJSON validation, haversine, hotspots, analytics engine |
| `test_admin.py` | 39 | 26 forbidden-role checks, reference defaults → managed, custom category routing, boundary-driven ward/zone auto-location, GeoJSON import with skips, settings, officer-name disclosure, audit log, officer profile and availability override, managed metadata IDs, **Cognito user admin (create, list, role change, disable, not-found, invalid role, self-protection)**, privilege-escalation attempt |
| `test_workflow_extended.py` | 21 | accept/decline/progress, reject with duplicate link, manual escalation, reopen window and limit, close feedback, priority & summary on create, duplicate detection privacy, rate limit, pagination (admin + citizen), GSI fallback, notification centre per role, analytics endpoint, level-2 escalation, configurable warning hours, DB/S3/config-table outages, oversized body |
| `test_end_to_end.py` | 1 | see below |
| `test_template_routes.py` | 2 | every handled route is in both templates and vice versa |

### End-to-end workflow (`test_end_to_end.py`)

Runs entirely against moto's Cognito, DynamoDB, S3 and SES, with **real ID-token claims** fed to the handler as API Gateway would:

1. Citizen self-registers (`sign_up`), is confirmed, and the real post-confirmation Lambda adds them to `Citizen`.
2. An admin invites an officer through `POST /admin/users`.
3. Citizen, officer and admin sign in with `USER_PASSWORD_AUTH`; claims are decoded from the issued ID tokens.
4. Citizen uploads evidence (presigned URL), submits a complaint with a map pin and idempotency key; the item is persisted.
5. Admin reviews it (evidence URL present), picks the officer from the directory, assigns.
6. Officer sees it in the list and the notification centre; the citizen cannot act as officer (403).
7. Officer accepts, starts, uploads proof, resolves with a note.
8. Citizen sees RESOLVED with proof and note; the resolution email was accepted by (mock) SES and recorded.
9. Citizen closes with a rating.
10. Admin analytics show 1 resolved, SLA met; the audit log contains the invitation.
11. History is exactly `created → assigned → accepted → work_started → resolved → closed`.

## Frontend suites

| File | Tests | Covers |
|---|---|---|
| `logic.test.js` | 32 | lifecycle, analytics (summary, compliance, grouping, trends, filters, CSV injection), geo, form validation, payload, officer ranking, browser aggregation, rejected SLA, password policy, query/slug helpers, admin-defined categories |
| `portals.test.jsx` | 16 | role routing (signed out, loading, no role, three portals), citizen stats/validation/submission/actions, officer actions and proof requirement, admin figures, assignment, filters/workload |
| `platform.test.jsx` | 17 | password-reset flow and policy, labelled login fields, duplicate warning + category suggestion, close with rating/feedback, notification centre, officer accept/decline, officer profile, admin nav hidden from officers, every admin page renders, ranked assignment, reject validation, department creation, GeoJSON import, staff invitation + audit, settings, server vs browser analytics |

## Manual browser verification (local stack)

`backend/tools/local_dev_server.py` (real Lambda code; AWS mocked by a moto server; demo logins mapped to real moto Cognito users) + Vite dev server:

| Check | Result |
|---|---|
| Admin creates a zone through the UI; persisted | ✅ (`east-test`) |
| Ward boundary import (synthetic test square, labelled as such) → new complaint auto-located in ward and zone | ✅ |
| Keyword + urgency priority scoring → CRITICAL; triage reasons hidden from citizen | ✅ |
| Duplicate check finds own nearby complaint (ID shown because it is the citizen's own) | ✅ |
| City map: category glyph/shape markers, status colours, boundary shading, legend | ✅ (7 markers, 1 boundary) |
| Analytics page uses server aggregation; all tables populated | ✅ |
| Officer portal: accept/decline/start on new work, progress/resolve on active work, daily workload | ✅ |
| Dialog focus and Escape-to-close | defect found → fixed (dialogs now focus their first field) |

## Defects found and fixed during this iteration

| Defect | How found | Fix |
|---|---|---|
| Decimal detection used `hasattr(v, "is_integer")`, which is also true for `int`/`bool` on Python 3.12+ → 500 on reference writes | new admin tests | explicit `isinstance(v, Decimal)` |
| `zone` is a DynamoDB reserved word → analytics projection failed | new analytics tests | all projected attributes aliased |
| Department matching in assignment ranking ignored legacy complaints without `departmentId` | new frontend test | resolve department name via reference data |
| Dialogs did not move focus into the first field | browser check | auto-focus + Escape to close |
| Component tests depended on the wall clock (fixtures relative to a fixed time) → failed after 15:00 UTC | repeated test run | system time pinned in component tests |

## Performance measurement (local, in-process)

`backend/tools/benchmark.py 300` — 300 synthetic complaints, handler invoked in-process against moto (no network, no API Gateway, no Lambda cold start, no real DynamoDB). Figures characterise the code path, **not** AWS latency.

| Operation | n | median | p95 |
|---|---|---|---|
| Create complaint (incl. nearby-duplicate scan, triage) | 300 | 319 ms | 687 ms |
| Admin list all (full scan, 300 items) | 10 | 1 208 ms | 1 299 ms |
| Citizen list own (GSI query) | 10 | 29 ms | 32 ms |
| Analytics aggregation (300 items) | 10 | 1 070 ms | 1 538 ms |
| Duplicate check | 10 | 584 ms | 612 ms |
| SLA sweep (300 open items) | 5 | 834 ms | 873 ms |

The 40× gap between the citizen GSI query and the full scan, and the scan-dominated create/duplicate paths, are consistent with the cost model's finding that full scans dominate at scale.

## Not covered

- **Nothing was tested against the deployed AWS stack.** API Gateway JWT validation, real SES delivery, real S3 CORS/IAM and the new GSI/config table are verified only after deployment (`DEPLOYMENT_GUIDE.md` §4).
- No load test on AWS; no accessibility audit (keyboard/screen-reader/contrast not systematically tested).
- Leaflet rendering is mocked in component tests and was checked manually in the browser.
- Amplify (Cognito) browser calls are mocked in frontend tests; the backend end-to-end test uses moto Cognito.
