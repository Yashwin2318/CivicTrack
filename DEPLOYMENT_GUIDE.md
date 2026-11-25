# CivicTrack — Deployment & Maintenance Guide

> Nothing in this repository has been deployed by the development work of 2026-11-09. Every command that changes AWS below must be run by the owner (or with the owner's explicit approval).

## 1. Prerequisites

| Tool | Status on the dev machine | Needed for |
|---|---|---|
| Node.js 24 / npm 11 | installed | frontend |
| Python 3.14 | installed | backend, tests |
| AWS CLI v2 | installed (profile `alertnet-developer`, read-only for some services) | deploy, admin |
| AWS SAM CLI | **not installed** | `sam build` / `sam deploy` — install from <https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html> |
| Docker | not required | the local dev server replaces `sam local` |

The deploying identity needs CloudFormation, Lambda, API Gateway, IAM (role update), S3 and DynamoDB permissions for the stack.

## 2. Verify before deploying

```bash
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install "moto[server]" pytest pytest-cov boto3 cfn-lint
```

```bash
backend/.venv/Scripts/python -m pytest backend/tests -q
```

```bash
backend/.venv/Scripts/cfn-lint infrastructure/template.yaml infrastructure/template.local.yaml
```

```bash
cd frontend && npm ci && npm run lint && npm test && npm run build
```

## Deployment record — 2026-11-10

| Step | Result |
|---|---|
| Pre-deploy backup | `civictrack-pre-deploy-20261110` (6 items) — AVAILABLE |
| Build | SAM CLI 1.171.0, `sam build` in `infrastructure/` (python3.14) |
| Change set | `samcli-deploy1794310518`: 30 add, 8 modify, 1 delete (old API deployment), 0 replacements |
| Execution | started 11:55:12 UTC, `UPDATE_COMPLETE` 11:57:09 UTC, no failures or rollback |
| Citizen GSI | backfill finished ~4.5 min after stack completion; ACTIVE with 6 items (citizen lists used the scan fallback meanwhile) |
| Smoke checks | `/health` 200; all new routes 401 without/with forged token; CORS preflight allows PUT; 6 complaints and 8 evidence objects intact; throttling 25/50; lifecycle rule enabled |
| First SLA sweep on new code (12:01 UTC) | 0 errors; 2 one-time level-2 escalation emails for the 2 previously escalated open complaints |

**Artifact bucket note:** the SAM-managed bucket (`aws-sam-cli-managed-default` stack) no longer exists, so future deploys must use the existing bucket instead of `resolve_s3`:

```bash
cd infrastructure && sam build && sam deploy --no-resolve-s3 --s3-bucket civictrack-sam-artifacts-<account-id>-ap-south-1 --s3-prefix civictrack-dev --parameter-overrides AllowedOrigin='*' SesSenderEmail=<verified-sender> AdminNotificationEmail=<admin-mailbox> EnableFrontendHosting=false
```

`SesSenderEmail` and `AdminNotificationEmail` no longer have defaults (no personal address in the repository), so pass the current values on every deploy. Set `EnableFrontendHosting=true` to add the hosting resources in §5.

Rollback point: the backup above plus `backend/_archive/app_deployed_2026-11-02.py` and `infrastructure/_archive/template_deployed_2026-11-02.yaml`.

## Deployment record — 2026-11-25

| Step | Result |
|---|---|
| Hosting attempt 1 (`EnableFrontendHosting=true`) | Rolled back: IAM user lacked `cloudfront:CreateOriginAccessControl`. Owner attached a managed `civictrack-cloudfront` policy. |
| Hosting attempt 2 | Rolled back: CloudFront returned "Your account must be verified before you can add new CloudFront resources". AWS Support case opened by the owner (account and billing). The empty retained `civictrack-frontend-…` bucket was deleted so a later attempt can recreate it. |
| Backend-only deploy (`EnableFrontendHosting=false`) | `UPDATE_COMPLETE`; 4 in-place modifications, no adds, deletes or replacements. Officer names now fall back to email. `/health` 200, protected routes 401 without a token; deployed `app.py` confirmed to contain the fix. |

**Next:** once AWS Support verifies the account, rerun the §2 deploy command with `EnableFrontendHosting=true`, then follow §5 to upload `frontend/dist` and invalidate the distribution.

## 2a. Deployment readiness review (2026-11-10)

Read-only checks run against the live account (no changes made):

| Check | Result |
|---|---|
| Stack `civictrack-dev` status | `UPDATE_COMPLETE`, last updated 2026-11-02 — nothing from this work has been deployed |
| Name collision for new table `CivicTrack-Config` | none (only `CivicTrack-Complaints` exists) |
| Live items compatible with the new GSI | 6 of 6 have non-empty string `citizenSub` and `createdAt` → all indexed |
| Live statuses / categories | `PENDING`, `RESOLVED` / `Other`, `Roads` — all handled by the new code |
| Templates | cfn-lint: 0 findings (both); router ↔ template parity test passes |

**Backward compatibility with the deployed application**

| Area | Impact |
|---|---|
| Existing complaints | Unchanged. Read with legacy mapping; new attributes added only when acted on. No migration. |
| Existing users / Cognito | Pool, client and groups unchanged (Retain policies are metadata). Group membership still drives roles. |
| Evidence in S3 | Existing objects untouched; key prefixes unchanged (new `progress/` prefix only for new uploads). |
| Old frontend + new backend | Compatible: existing routes keep their shapes; errors gain a `code` field; categories the old form offers are all built-in. |
| New frontend + old backend | Degrades gracefully: reference data, analytics and notifications fall back; admin management screens show errors until the backend is deployed. **Deploy the backend first.** |
| Rollback | Archived code/template in `_archive/`; complaints moved to `ASSIGNED`/`REOPENED`/`CLOSED`/`REJECTED` by the new code would be unknown to the old code (manual decision). The config table and GSI would be removed from the stack only if the archived template is deployed; `DeletionPolicy: Retain` on the new table keeps its data. |

**Risks**

| Risk | Likelihood / impact | Mitigation |
|---|---|---|
| Change set shows replacement of a data resource | low / high | Review change set; abort on any `Replacement: True` for pool, client, groups, tables, bucket |
| Cognito invitation emails limited (default Cognito email sending quota is small) | medium / low | Configure Cognito to send through SES for volume |
| API throttling (25 rps, burst 50) too low for peaks | low / medium | Adjust `MethodSettings` |
| Scan-heavy endpoints slow as data grows | grows with data / medium | Phase 11 in `IMPLEMENTATION_ROADMAP.md` |
| SES sandbox: citizens do not receive email | certain until fixed / medium | Request production access |
| CORS `*` | existing / low | Set `AllowedOrigin` after hosting the frontend |

**Pre-deploy checklist**

1. Commit the working tree (nothing is committed for this iteration yet).
2. Install SAM CLI; run the test, lint and template commands in §2.
3. `cd infrastructure && sam build && sam deploy` — review the change set against §3; answer **N** on anything unexpected.
4. Smoke test (§4), then follow "After the backend deploy" below.
5. Build the frontend with a `.env.production` created from `frontend/.env.example`.

## 3. Deploy the backend (owner approval required)

> **Untested commands.** SAM CLI was not installed on the development machine, so the two commands below have not been executed. They rely on SAM's defaults: run from `infrastructure/`, SAM reads `samconfig.toml` next to `template.yaml` and builds into `infrastructure/.aws-sam/` (git-ignored). `CodeUri: ../backend/...` resolves relative to the template.

```bash
cd infrastructure && sam build
```

```bash
cd infrastructure && sam deploy
```

`samconfig.toml` has `confirm_changeset = true`, so SAM prints the change set and waits for **y/N**. Expected rows:

| Resource | Action | Why |
|---|---|---|
| ConfigTable (`CivicTrack-Config`) | **Add** | new reference/settings/audit table (additive) |
| ComplaintsTable | Modify (Replacement: **False**) | adds GSI `citizenSub-createdAt-index` online; retention policy metadata |
| CivicTrackUserPool, EvidenceBucket | Modify (Replacement: False) or no change | `DeletionPolicy/UpdateReplacePolicy: Retain` (metadata); bucket lifecycle rule |
| ComplaintsFunction | Modify | new code; env vars `CONFIG_TABLE`, `CITIZEN_INDEX`, `VIEW_URL_TTL_SECONDS` |
| ComplaintsExecutionRole | Modify | config-table access; Cognito admin actions scoped to this pool ARN; `DeleteItem` removed |
| CivicTrackApi | Modify | 28 new routes, `PUT` in CORS |
| `CivicTrackApiDeployment<hash>` (new) | Add | SAM creates a new API deployment whenever routes change |
| `CivicTrackApiDeployment<hash>` (old) | **Remove — expected** | the superseded deployment snapshot; the `dev` stage moves to the new one |
| CivicTrackApidevStage | Modify | new deployment + throttling (25 rps, burst 50) |
| ComplaintsFunction…Permission… (×28) | Add | invoke permissions for the new routes |

**Stop and answer N** if any row shows `Remove` or `Replacement: True` for `CivicTrackUserPool`, `CivicTrackUserPoolClient`, the Cognito groups, `ComplaintsTable` or `EvidenceBucket`, or if any resource not listed above changes.

**GSI build:** DynamoDB backfills the new index in the background (seconds for the current 6 items). CloudFormation waits for it to become ACTIVE. Until then the citizen list uses the built-in scan fallback, so nothing breaks.

**Compatibility:** existing records need no migration. `PENDING`+officer rows are read as `ASSIGNED`; the first action on an old row creates its `history` list. With an empty config table the API serves the built-in departments and categories.

### After the backend deploy (admin portal, no AWS console needed)

1. **Departments** → *Save defaults now* (persists the built-in departments/categories so they can be edited).
2. **Users & Roles** → invite officers; **Officers** → set each officer's department, zones/wards, availability and workload cap.
3. **Zones & Wards** → create zones/wards, or import the official boundary GeoJSON with its source and licence. Complaints are only auto-located once boundaries exist; **no boundaries are shipped with CivicTrack**.
4. **Settings** → confirm the SLA hours (48), warning hours (4) and the citizen rate limit.

**Rollback:** the code and template running in AWS before this work are kept in git:

| File | Is |
|---|---|
| `backend/_archive/app_deployed_2026-11-02.py` | byte-identical to the deployed `CivicTrack-Complaints` code (verified against the SAM build that was deployed) |
| `infrastructure/_archive/template_deployed_2026-11-02.yaml` | the template that produced the deployed stack (semantically identical to `aws cloudformation get-template`) |

To roll back: copy the archived file over `backend/complaints/app.py` (and delete `backend/complaints/lifecycle.py` from the build, or leave it — the old code does not import it), copy the archived template over `infrastructure/template.yaml`, then `sam build` and `sam deploy`. Data written by the new code (extra attributes, `ASSIGNED`/`REOPENED`/`CLOSED` statuses) remains in the table; the old code ignores unknown attributes, but complaints in the new statuses would need a manual decision.

## 4. Smoke test after deploy

```bash
curl -s https://4s044dr8u8.execute-api.ap-south-1.amazonaws.com/dev/health
```

Then, signed in through the frontend: citizen submits with a pin and photo → admin sees it on the map, assigns from the officer list → officer starts, uploads proof, resolves → citizen sees proof and closes. Check the Lambda log for `SLA CHECK SUMMARY` within 15 minutes; `errors` should be 0.

## 5. Frontend

```bash
cd frontend && npm run build
```

`.env` files are git-ignored, so a fresh clone has none. Before building, copy `frontend/.env.example` to `frontend/.env.production` and fill in the API URL, user pool ID and app client ID (the stack outputs `ApiUrl`, `UserPoolId`, `UserPoolClientId`), with `VITE_ENABLE_DEMO_AUTH=false`. Without it the build still succeeds but points at `http://127.0.0.1:3000` with no Cognito configuration. The build output in `frontend/dist/` is static and can be hosted anywhere.

**Optional hosting in the stack (needs approval).** Deploying with `EnableFrontendHosting=true` adds a private S3 bucket, a CloudFront origin access control, a CloudFront distribution (HTTPS, SPA fallback to `index.html`) and a bucket policy that only CloudFront can read. At pilot traffic this stays inside the CloudFront always-free tier (1 TB and 10 million requests a month) plus a few MB of S3 storage. Then publish the build:

```bash
aws s3 sync frontend/dist s3://<FrontendBucketName> --delete
```

```bash
aws cloudfront create-invalidation --distribution-id <FrontendDistributionId> --paths "/*"
```

Open the `FrontendUrl` stack output. Once it works, consider redeploying with `AllowedOrigin=<FrontendUrl>` so API and evidence CORS stop accepting `*` (local development against the live API then stops working, which is the intended trade-off).

## 6. Recommended one-off changes (each needs approval)

| Change | Command / action |
|---|---|
| Log retention 30 days (audit F6) | `aws logs put-retention-policy --log-group-name /aws/lambda/CivicTrack-Complaints --retention-in-days 30 --region ap-south-1` (repeat for `CivicTrack-PostConfirmation`). In Git Bash prefix with `MSYS_NO_PATHCONV=1`. |
| DynamoDB PITR (F7, billing) | set `PointInTimeRecoveryEnabled: true` in `template.yaml`, deploy |
| SES production access (F8) | SES console → Account dashboard → Request production access |
| GSIs for scale (F4, billing) | design in `AWS_BUDGET_AUDIT.md` B1 — adding a GSI is an online, non-destructive table update |
| Budget alert | AWS Budgets → monthly cost budget with 80% email alert |

## 7. Local development (no Docker, no AWS)

```bash
backend/.venv/Scripts/python backend/tools/local_dev_server.py --seed
```

```bash
cd frontend && npm run dev
```

Open <http://localhost:5173> and use the **Preview Citizen / Officer / Admin Portal** buttons (enabled by `frontend/.env.local`). All AWS calls go to an in-memory moto server; the script refuses to run if the endpoint is not localhost. `POST http://127.0.0.1:3000/__dev/sla-sweep` runs the SLA job on demand. Seeded titles start with `[Sample]`.

## 8. Routine maintenance

| Task | How |
|---|---|
| Add an officer | Create the user in Cognito, then `aws cognito-idp admin-add-user-to-group --user-pool-id ap-south-1_IfgVg8fLu --username <email> --group-name Officer`. They appear in the admin officer picker immediately. |
| Remove an officer | Unassign or reassign their open complaints, then disable the user in Cognito (disabled officers cannot be assigned). |
| Promote an admin | Add the user to the `Admin` group. Group changes apply at the user's next sign-in. |
| Watch SLA automation | CloudWatch Logs Insights: `filter @message like /SLA CHECK SUMMARY/` — check `errors` and `malformedTimestamps`. |
| Watch email delivery | Each complaint stores `lastCitizenNotification.sent`; `false` usually means SES sandbox rejected an unverified address. |
| Dependency updates | `npm outdated` / `npm audit --omit=dev` in `frontend`; `pip list --outdated` in the backend venv. Re-run all tests. |
| History size | Each complaint keeps up to 200 history entries (guard in `apply_transition`). |
