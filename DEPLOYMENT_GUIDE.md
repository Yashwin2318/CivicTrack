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

## 3. Deploy the backend (owner approval required)

```bash
sam build --template-file infrastructure/template.yaml --build-dir .aws-sam/build
```

```bash
sam deploy --template-file .aws-sam/build/template.yaml --config-file infrastructure/samconfig.toml
```

`samconfig.toml` has `confirm_changeset = true`, so SAM prints the change set and waits. **Expected change set** — check that every row is `Modify` or `Add`, and that **no row shows `Replacement: True` or `Remove`** for the user pool, table or bucket:

| Resource | Action | Why |
|---|---|---|
| ComplaintsFunction | Modify | new code, new env var |
| ComplaintsExecutionRole | Modify | `dynamodb:DeleteItem` removed |
| EvidenceBucket | Modify | lifecycle rule added (no replacement) |
| CivicTrackApi + new `AWS::ApiGateway::Deployment` | Modify / Add | six new routes |
| ComplaintsFunction*Permission (×6) | Add | invoke permissions for new routes |

If anything else appears (especially on `CivicTrackUserPool`, `ComplaintsTable`, `EvidenceBucket` replacement), answer **N** and investigate.

**Compatibility:** existing records need no migration. `PENDING`+officer rows are read as `ASSIGNED`; the first action on an old row creates its `history` list.

**Rollback:** the pre-change sources are archived in the session scratchpad (`original-snapshot/civictrack-original.tar`) — copy it into the repo before relying on it; also commit the current state to git first (the repository had no commits). To roll back, check out the previous `backend/complaints/app.py`, restore `template.yaml`, and `sam deploy` again. Data written by the new code (extra attributes) is ignored by the old code.

## 4. Smoke test after deploy

```bash
curl -s https://4s044dr8u8.execute-api.ap-south-1.amazonaws.com/dev/health
```

Then, signed in through the frontend: citizen submits with a pin and photo → admin sees it on the map, assigns from the officer list → officer starts, uploads proof, resolves → citizen sees proof and closes. Check the Lambda log for `SLA CHECK SUMMARY` within 15 minutes; `errors` should be 0.

## 5. Frontend

```bash
cd frontend && npm run build
```

`frontend/.env.production` already points at the deployed API and user pool. The build output in `frontend/dist/` is static and can be hosted anywhere. Hosting on S3 + CloudFront would add new AWS resources (small cost) and is **not** included — decide the hosting target, then set the stack parameter `AllowedOrigin` to that exact origin (e.g. `https://civictrack.example.org`) and redeploy so API and S3 CORS stop accepting `*`.

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
