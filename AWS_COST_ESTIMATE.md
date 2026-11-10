# CivicTrack — AWS Cost Estimate (monthly and annual)

> **These are estimates, not billing.** They come from `AWS_COST_MODEL.xlsx` (regenerate with `python docs/tools/build_cost_model.py AWS_COST_MODEL.xlsx`) using **assumed** ap-south-1 list prices and **assumed** usage. Verify every unit price at <https://calculator.aws/> and read actual spend from AWS Billing → Bills. No billing data was retrieved for this document. Free-tier allowances are **not** deducted (conservative).

## Scenarios (workbook defaults)

| Driver | Current (dev) | Pilot ward | City scale |
|---|---|---|---|
| New complaints / month | 10 | 1 000 | 50 000 |
| Complaints stored | 10 | 6 000 | 300 000 |
| List loads / month | 300 | 15 000 | 400 000 |
| Admin analytics / feed loads | 30 / 100 | 600 / 3 000 | 3 000 / 20 000 |
| Monthly active users | 20 | 2 000 | 60 000 |

## Estimated cost

| Line item | Dev / month | Pilot / month | City / month |
|---|---|---|---|
| Lambda (requests + compute) | $0.01 | $0.12 | $14.64 |
| API Gateway (REST) | $0.00 | $0.10 | $4.02 |
| DynamoDB reads — list scans | $0.00 | $9.62 | $12 825.00 |
| DynamoDB reads — SLA sweeps | $0.00 | $0.92 | $46.17 |
| DynamoDB reads — duplicate checks | $0.00 | $0.06 | $80.16 |
| DynamoDB reads — analytics & admin feed | $0.00 | $1.15 | $368.72 |
| DynamoDB writes + storage (incl. GSI) | $0.00 | $0.03 | $1.34 |
| S3 (storage + requests) | $0.00 | $0.35 | $33.89 |
| Data transfer out (photo views) | $0.01 | $1.20 | $60.04 |
| SES email | $0.01 | $0.70 | $35.00 |
| CloudWatch Logs | $0.04 | $0.68 | $17.00 |
| Cognito (above free MAU) | $0.00 | $0.00 | $275.00 |
| EventBridge schedule | $0.00 | $0.00 | $0.00 |
| **Monthly total** | **≈ $0.07** | **≈ $14.94** | **≈ $13 761** |
| **Annual total (×12)** | **≈ $0.88** | **≈ $179** | **≈ $165 132** |

Rows are rounded; totals are the workbook's formula results. The list-scan row conservatively assumes every list load is a full scan. After deployment, citizen lists use the new GSI query, so the real figure would be lower by the citizen share of list loads.

## What drives cost

1. **Full-table scans dominate at scale** (≈ 79% of the pilot estimate, ≈ 97% at city scale). Every admin/officer list, the SLA sweep, analytics, the admin notification feed and duplicate checks read the whole table or all open items. The local benchmark shows the same shape: citizen GSI query 29 ms vs full scan 1 208 ms at 300 records (`docs/TESTING_REPORT.md`).
2. Everything else is small and linear in traffic.

## Services that can incur charges

| Service | Charged for | Notes |
|---|---|---|
| DynamoDB | read/write request units, storage, GSI copy | on-demand; PITR would add a storage-proportional charge (not enabled) |
| Lambda | requests, GB-seconds | 512 MB; review after measuring real durations |
| API Gateway (REST) | requests | throttled at 25 rps / burst 50 |
| S3 | storage, requests, data transfer out | no evidence expiry (policy decision) |
| SES | emails sent | sandbox until production access is granted |
| CloudWatch Logs | ingestion, storage | retention currently never-expire (30 days recommended) |
| Cognito | MAU above the free tier | verify the pool's tier |
| EventBridge | scheduled rules | no charge |
| OpenStreetMap tiles / Nominatim | — | free community services with usage policies; heavy public use needs a paid or self-hosted provider (**not** adopted without approval) |

No EC2, NAT Gateway, RDS, load balancer or other always-on infrastructure is used.

## Cost actions (each needs owner approval)

| Action | Effect |
|---|---|
| Paginate admin/officer lists in the UI (API already supports `limit`/`nextToken`) | cuts the largest line item |
| Sparse GSI for open complaints (SLA sweep, duplicate checks) | removes repeated full scans |
| Officer GSI after normalising empty `assignedOfficerSub` to absent | officer lists become queries |
| Persist daily analytics roll-ups (scheduled job) | analytics loads stop scanning |
| Store notifications per user instead of deriving them | admin feed stops scanning |
| CloudWatch log retention 30 days; AWS Budgets alert | caps log storage; early warning |
