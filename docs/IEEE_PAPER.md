# CivicTrack: A Serverless, GIS-Enabled Platform for Accountable Municipal Complaint Management

*Draft manuscript in IEEE conference structure. Transfer to the official IEEE conference template (e.g. `IEEEtran`, conference mode) before submission; author names, affiliations and venue are to be added by the authors.*

> **Integrity statement.** All quantitative results in this draft were produced by the authors' own runs on 2026-11-09/10 and are reproducible from the repository (`backend/tests`, `frontend/src/test`, `backend/tools/benchmark.py`, `AWS_COST_MODEL.xlsx`). Performance figures were measured **in-process against an AWS emulator (moto) on a laptop**; they characterise code-path cost and are **not** measurements of AWS latency. Cost figures are **model estimates** from assumed list prices, not billing data. No user study was conducted. The six references were checked against publisher or repository records.

---

**Abstract**—Municipal complaint systems often fail citizens not for lack of reporting channels but for lack of accountability: complaints are not routed, deadlines are not enforced, and resolution is not evidenced. We present CivicTrack, a serverless civic complaint platform built on AWS managed services (Cognito, API Gateway, Lambda, DynamoDB, S3, SES, EventBridge) with three role-specific web portals for citizens, field officers and administrators. CivicTrack enforces an explicit seven-state complaint lifecycle with optimistic-concurrency guards and an append-only audit history, requires photographic proof for resolution, and applies an idempotent service-level-agreement (SLA) monitor that issues exactly-once warnings and two escalation levels despite at-least-once scheduler delivery. A geographic module locates complaints in administrator-imported ward boundaries by point-in-polygon tests and computes hotspots, while a transparent rule-based triage component suggests categories, scores priority with stated reasons and flags nearby duplicates without disclosing other citizens' data. We evaluate the implementation with 221 backend and 65 frontend automated tests, including an end-to-end workflow driven by emulated Cognito identity tokens, and with an in-process micro-benchmark that exposes the cost of scan-based access patterns: a secondary-index query returned a citizen's complaints in a median 29 ms versus 1 208 ms for a full scan of 300 records in the emulator. A cost model estimates single-digit to low double-digit US dollars per month at pilot-ward scale and identifies full-table scans as the dominant cost at city scale.

**Index Terms**—e-governance, smart cities, serverless computing, complaint management, geographic information systems, service level agreements, Amazon Web Services.

## I. Introduction

Cities increasingly expose digital channels for residents to report civic problems such as potholes, broken streetlights and uncollected waste. Treating residents as distributed observers of urban conditions is an instance of what Goodchild termed "citizens as sensors" [1]. The Internet of Things literature similarly envisions city services that integrate heterogeneous data sources into open digital services [2]. Yet collecting reports is only the first step; the value to residents depends on whether reports are routed to the responsible department, acted on within a committed time, and closed with verifiable evidence.

This paper describes CivicTrack, an implemented platform that focuses on that accountability loop. Its contributions are:

1. an explicit, backward-compatible complaint state machine with concurrency-safe transitions and per-complaint audit history (Section IV-B);
2. an idempotent SLA monitor that tolerates duplicate scheduler delivery, retries and concurrent executions (Section IV-C);
3. a GIS module that locates complaints using administrator-supplied GeoJSON boundaries and never ships fabricated boundaries (Section IV-D);
4. an explainable, rule-based triage component with privacy-preserving duplicate detection (Section IV-E);
5. an evaluation through automated tests, an emulator micro-benchmark and a cost model, reported with their limitations (Sections V–VI).

## II. Related Work

**Volunteered and user-generated geographic data.** Goodchild characterised volunteered geographic information as citizens acting as sensors and raised questions of accuracy, motivation and privacy [1]. OpenStreetMap demonstrated that peer-produced street maps can reach broad coverage [3]; CivicTrack uses OpenStreetMap tiles and geocoding for its maps.

**Smart-city platforms.** Zanella et al. surveyed IoT architectures for smart cities and reported an urban deployment in Padova [2]. Their focus is sensing infrastructure; CivicTrack addresses the human-reported, workflow-centric side of city services.

**Civic issue reporting standards.** The Open311 GeoReport v2 specification defines a common API for services and service requests with status and location [4]. CivicTrack's model is compatible in spirit (service categories, geolocated requests, status) but adds internal workflow states, evidence requirements and SLA escalation that GeoReport does not prescribe.

**Serverless and key-value storage.** Jonas et al. argued that serverless computing simplifies cloud programming by removing server management, while noting limitations around state and performance [5]. DynamoDB descends from the design lineage of Amazon's Dynamo key-value store [6]; our results on scan- versus key-based access reflect the access-pattern discipline that such stores require.

## III. Problem Statement

Given (i) citizens who report geolocated issues with optional photographs, (ii) field officers organised by department and area, and (iii) administrators accountable for service levels, design a system that:

- **R1** routes every complaint to a department and records every state change with actor and time;
- **R2** prevents invalid or conflicting state changes under concurrent use;
- **R3** enforces a resolution deadline (48 h by default) with warnings and escalation that are neither lost nor duplicated;
- **R4** requires photographic proof before a complaint is marked resolved;
- **R5** isolates data: citizens see only their own complaints, officers only those assigned to them;
- **R6** supports area-based management without inventing official boundaries;
- **R7** remains low-cost at municipal pilot scale with no always-on servers.

## IV. Proposed Architecture and Methodology

### A. System architecture

The browser single-page application (React) authenticates against an Amazon Cognito user pool; users belong to one of three groups (Citizen, Officer, Admin). API Gateway validates Cognito ID tokens and forwards requests to a single Python 3.14 Lambda function, which derives the role solely from the token's group claim and enforces authorization on every route. Complaints are stored in a DynamoDB table keyed by complaint ID with a global secondary index on (citizen, creation time). A second table stores reference data (departments, category routing, zones, wards with boundaries, officer profiles, settings) and a monthly-partitioned audit log. Evidence photographs are uploaded directly from the browser to a private S3 bucket through server-generated, user-scoped presigned URLs that expire after 300 s, and are verified by size and content type before being attached. SES sends notifications; EventBridge invokes the SLA monitor every 15 minutes.

### B. Lifecycle and concurrency (R1, R2, R4)

The lifecycle has seven states: PENDING, ASSIGNED, IN_PROGRESS, RESOLVED, REOPENED, CLOSED and REJECTED. Transitions are declared in a table (e.g. *start*: ASSIGNED or REOPENED → IN_PROGRESS; *resolve*: IN_PROGRESS → RESOLVED, only with a verified proof photograph). Each handler reads the item with a consistent read, checks the transition, and writes with a DynamoDB condition that both the status and the assigned officer are unchanged since the read. A concurrent or repeated request therefore fails with HTTP 409 instead of overwriting newer state. Each successful write appends an entry {time, action, actor role, actor, from, to, note} to the complaint's history. Legacy records written by the earlier deployed version (only three states) are interpreted without migration: PENDING with an assigned officer is treated as ASSIGNED.

### C. Idempotent SLA monitoring (R3)

Scheduled events may be delivered more than once, and Lambda may retry. For each open complaint the monitor determines at most one due notification (warning in the final configurable window, breach, or a second-level escalation 24 h after the deadline). Before sending, it *claims* a per-stage flag with a conditional write that succeeds only if the flag is absent; only the claimant sends. If sending fails, the claimant removes the flag only if it still holds its own timestamp, so the next run retries. Errors on one record are isolated and counted. Reopening a complaint restarts its SLA clock and clears the flags.

### D. Geographic module (R6)

Administrators import zones and wards as GeoJSON FeatureCollections. Each geometry is validated (Polygon/MultiPolygon, closed rings, coordinate ranges, ≤20 000 vertices, ≤300 KB) and stored with its bounding box and declared data source. On submission, a complaint with coordinates is located by bounding-box pre-filtering followed by even-odd ray casting that respects holes. Hotspots are computed by binning coordinates into cells of 0.005° (about 500 m) and reporting cells with at least three complaints. No boundary data is bundled: the system reports "no boundary" rather than guessing.

### E. Explainable triage and privacy (R1, R5)

A keyword lexicon suggests a category, weighting title matches double. Priority is a bounded score: a category base plus weights for risk terms (e.g. electrical or fire hazard, proximity to schools or hospitals), up to +20 for nearby open complaints of the same category within 200 m, and an adjustment for the citizen's stated urgency. The score and its reasons are stored and shown to staff only. The pre-submission duplicate check returns, for other citizens' complaints, only distance, status and category, never identifiers or text.

### F. Security controls

Role derivation solely from validated tokens; 404 (not 403) for other citizens' complaints to prevent identifier probing; administrator actions behind a single guard; Cognito administrative permissions scoped to the one user pool; per-citizen hourly submission caps and API throttling; uniform error bodies without internal detail; JSON logs that exclude email addresses and complaint text; and retention policies that prevent stack operations from deleting data resources.

## V. Implementation and Experimental Setup

**Implementation.** Backend: 2 862 lines of Python (including comments and blank lines) across six modules (`app`, `lifecycle`, `intelligence`, `geo`, `analytics_engine`, `reference`). Frontend: React 19 with Vite, Leaflet for maps, lazy-loaded per-role bundles. Infrastructure: one AWS SAM template validated with cfn-lint.

**Test environment.** Windows 11, Intel x64 with 8 logical CPUs, Python 3.14.0, Node.js 24.16.0. AWS services were emulated with moto 5.2.5 (DynamoDB, S3, SES, Cognito). Frontend tests ran in jsdom with Vitest 5.

**Benchmark.** `backend/tools/benchmark.py` creates 300 synthetic complaints spread over 100 citizen accounts (to stay under the per-citizen rate limit) and invokes the Lambda handler in-process. It excludes API Gateway, cold starts, network latency and real DynamoDB behaviour.

**Cost model.** A spreadsheet model (`AWS_COST_MODEL.xlsx`) with assumed ap-south-1 list prices and three usage scenarios; free tiers are not deducted.

## VI. Results

### A. Functional verification

| Suite | Tests | Passed | Statement/line coverage |
|---|---|---|---|
| Backend (unit, API, authorization, lifecycle, SLA, notifications, GIS, analytics, admin, failure handling, end-to-end) | 221 | 221 | 92% |
| Frontend (logic, components, authentication flows, navigation, role visibility) | 65 | 65 | 62.7% |

Notable verified behaviours: a full workflow from self-registration through resolution and closure using identity tokens issued by the emulated Cognito pool; exactly-once SLA warnings and escalations under simulated duplicate delivery and send failure; rejection of administrator routes for 26 role/route combinations; and suppression of other citizens' identifiers in duplicate detection. Implementation surfaced five defects (e.g. Python 3.12+ `int.is_integer` breaking Decimal detection, a DynamoDB reserved word in a projection, clock-dependent tests), all fixed and covered by tests.

### B. Emulator micro-benchmark (300 records)

| Operation | n | Median (ms) | p95 (ms) |
|---|---|---|---|
| Create complaint (incl. nearby scan and triage) | 300 | 319 | 687 |
| Administrator list (full scan) | 10 | 1 208 | 1 299 |
| Citizen list (GSI query) | 10 | 29 | 32 |
| Analytics aggregation | 10 | 1 070 | 1 538 |
| Duplicate check | 10 | 584 | 612 |
| SLA sweep | 5 | 834 | 873 |

### C. Cost model (estimates)

| Scenario | Complaints / month | Estimated cost / month | Scan share |
|---|---|---|---|
| Development | 10 | ≈ $0.07 | 3% |
| Pilot ward | 1 000 | ≈ $14.94 | 79% |
| City scale | 50 000 | ≈ $13 761 | 97% |

## VII. Discussion

The lifecycle guard and claim-then-send pattern address R2 and R3 with conditional writes alone, without locks or queues, which suits a serverless deployment. The benchmark and the cost model agree qualitatively: operations that read whole tables (administrator lists, SLA sweep, analytics, duplicate checks) dominate both latency in the emulator and estimated cost at scale, whereas key-based access via an index was about forty times faster in the emulator. This is consistent with the access-pattern discipline associated with Dynamo-style stores [6] and indicates where indexing and pre-aggregation should be applied before city-wide deployment. Keeping triage rule-based made every priority decision explainable to administrators, at the cost of recall for complaints that use vocabulary outside the lexicon.

## VIII. Limitations

- No evaluation on AWS: authorizer behaviour, IAM scoping, SES delivery and real latency are unverified until deployment; emulator timings are not representative of DynamoDB.
- No user study, usability evaluation or accessibility audit.
- No official ward boundaries were available; area analytics depend on administrator imports.
- The triage lexicon was authored, not learned or validated against labelled complaints; its accuracy is unmeasured.
- Several operations remain scan-based; the cost model assumes list prices that must be verified.
- Three coarse roles; department-scoped administration is not supported.

## IX. Future Work

Deployment and measurement on AWS; sparse indexes for open complaints and pre-aggregated analytics; an Open311 GeoReport v2 [4] facade for interoperability; evaluation of the triage lexicon against labelled municipal data; accessibility and usability studies with residents and officers; and integration of official open boundary datasets with licence tracking.

## X. Conclusion

CivicTrack demonstrates that an accountable civic complaint workflow—routing, enforced deadlines, evidence-based resolution, auditability and area-based management—can be built entirely from managed serverless services with conditional writes as the main consistency mechanism. Automated tests verify the intended behaviour in an emulated environment, and both an emulator benchmark and a cost model point to scan-based access patterns as the principal obstacle to city-scale operation.

## References

[1] M. F. Goodchild, "Citizens as sensors: the world of volunteered geography," *GeoJournal*, vol. 69, no. 4, pp. 211–221, 2007, doi: 10.1007/s10708-007-9111-y.

[2] A. Zanella, N. Bui, A. Castellani, L. Vangelista, and M. Zorzi, "Internet of Things for smart cities," *IEEE Internet Things J.*, vol. 1, no. 1, pp. 22–32, Feb. 2014, doi: 10.1109/JIOT.2014.2306328.

[3] M. Haklay and P. Weber, "OpenStreetMap: User-generated street maps," *IEEE Pervasive Comput.*, vol. 7, no. 4, pp. 12–18, Oct.–Dec. 2008, doi: 10.1109/MPRV.2008.80.

[4] Open311, "GeoReport v2," Open311 specification wiki. [Online]. Available: http://wiki.open311.org/GeoReport_v2 (archived copy: https://webarchive.library.unt.edu/web/20170118233000mp_/http://wiki.open311.org/GeoReport_v2).

[5] E. Jonas *et al.*, "Cloud programming simplified: A Berkeley view on serverless computing," arXiv:1902.03383, Feb. 2019, doi: 10.48550/arXiv.1902.03383.

[6] G. DeCandia *et al.*, "Dynamo: Amazon's highly available key-value store," in *Proc. 21st ACM Symp. Operating Systems Principles (SOSP)*, Stevenson, WA, USA, 2007, pp. 205–220, doi: 10.1145/1294261.1294281.

*Verification note:* [1], [2], [3], [5] and [6] were checked against publisher, DOI or repository records (UCL Discovery, University of Padova repository, arXiv, ACM/SOSP records). [4] is a web specification; the live wiki was not opened directly and an archived copy was located, so the URL should be re-checked before submission. The month for [3] (Oct.–Dec.) follows the journal's quarterly issue 4 and should be confirmed in IEEE Xplore.
