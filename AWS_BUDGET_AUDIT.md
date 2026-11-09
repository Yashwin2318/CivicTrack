# CivicTrack — AWS Budget Audit

> **Estimates vs actuals.** Every figure in this document and in `AWS_COST_MODEL.xlsx` is an **estimate** built from assumed public list prices and assumed usage. **No actual billing data was retrieved.** Unit prices must be verified at <https://calculator.aws/> for ap-south-1. Read actual spend from AWS Billing → Bills (free) and enter it in the workbook's *Actual Billing* sheet.

## Summary

The stack is fully serverless and pay-per-use: no EC2, NAT Gateway, load balancer, RDS or provisioned capacity. At the current development volume (6 complaints) the estimated monthly cost is **well under US $1** before free tier, and in practice likely covered by free-tier allowances.

| Scenario (editable in workbook) | Complaints / month | Estimated monthly total* | Largest line item |
|---|---|---|---|
| Current (dev) | 10 | ≈ $0.07 | CloudWatch Logs |
| Pilot ward | 1,000 | ≈ $13.71 | DynamoDB list scans (≈ $9.62) |
| City scale | 50,000 | ≈ $13,311 | DynamoDB list scans (≈ $12,825, ~96%) |

\*Formula results from the workbook with its default assumptions, before free tier. Change the inputs and totals recalculate.

## Findings

| # | Area | Finding | Recommendation | Approval needed |
|---|---|---|---|---|
| B1 | DynamoDB | `GET /complaints` runs a strongly consistent **full-table Scan** for every role and every page load; the SLA sweep scans the full table 2,880×/month. Cost is O(table size × loads) and dominates at scale. | Add GSIs `citizenSub` and `assignedOfficerSub` (Query), and a sparse GSI for open complaints for the sweep. Add pagination for admin lists. | Yes — table schema change + deploy |
| B2 | CloudWatch | Log groups never expire. | 30-day retention (command in `DEPLOYMENT_GUIDE.md`). Code change already removes recipient emails from logs. | Yes — modifies AWS resource |
| B3 | Lambda | 512 MB / 60 s for an I/O-bound function. | Measure p95 duration in CloudWatch; try 256 MB. Keep timeout ≥ 30 s for the sweep. | Yes — deploy |
| B4 | S3 | No lifecycle rules; evidence kept indefinitely. | Template now aborts incomplete multipart uploads. Evidence **retention period is a policy decision** (legal/RTI requirements) — no expiry was added. | Yes — deploy; retention needs owner decision |
| B5 | DynamoDB backup | PITR disabled. | Enable PITR (small, storage-proportional charge). | Yes — billing change |
| B6 | SES | Sandbox: 200/day, verified recipients only. | Request production access before public launch. No cost change at this volume. | Yes — AWS request |
| B7 | API Gateway | REST API at ~$3.50/M requests. | HTTP API is cheaper but changes authorizer/CORS behaviour; **not recommended now** (migration risk > savings at current volume). | — |
| B8 | Cognito | Well under free MAU allowance. | None. | — |
| B9 | Maps | OpenStreetMap tiles + Leaflet: no AWS or vendor charge. OSM tile usage policy applies; for heavy production traffic consider a tile provider plan. | Review OSM tile policy before public launch. | Possibly — paid tile service |

## Cost guardrails (recommended, not created)

- AWS Budgets: a monthly cost budget (e.g. $5) with an email alert at 80%. The first two budgets per account are free; creating one modifies account configuration, so it was **not** done.
- Cost allocation tag `project=civictrack` on stack resources.
