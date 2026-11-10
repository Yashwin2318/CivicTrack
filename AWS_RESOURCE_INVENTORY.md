# CivicTrack — AWS Resource Inventory

Account `9862…` (masked), region **ap-south-1**, CloudFormation stack **`civictrack-dev`** (status `UPDATE_COMPLETE`, last updated 2026-11-02). Verified read-only on 2026-11-09 (not re-queried on 2026-11-10; no AWS call was made in later work). The last column lists the changes from both local iterations; **all of them were deployed on 2026-11-10** (stack `UPDATE_COMPLETE` 11:57 UTC). Every resource below is defined in `infrastructure/template.yaml`; no resource exists outside the stack except the SAM artifact bucket and the Lambda-created log groups.

| Logical ID | Type | Physical name / identifier | Purpose | Change in this work (deployed 2026-11-10) |
|---|---|---|---|---|
| CivicTrackUserPool | Cognito User Pool | `CivicTrack-Users` · `ap-south-1_IfgVg8fLu` | Identities; email login; strong password policy | `DeletionPolicy`/`UpdateReplacePolicy: Retain` (metadata only) |
| CivicTrackUserPoolClient | Cognito App Client | `CivicTrack-Web-Client` · `2k31829dp05hbdogg9qbuegvk` | Public SPA client (no secret) | none (recommend dropping `ALLOW_USER_PASSWORD_AUTH`) |
| CivicTrackCitizenGroup / OfficerGroup / AdminGroup | Cognito groups | `Citizen`, `Officer`, `Admin` | Role source for authorization | none |
| CivicTrackApi | API Gateway REST | `CivicTrack-API`, stage `dev` · `https://4s044dr8u8.execute-api.ap-south-1.amazonaws.com/dev/` | Cognito-authorized API | +28 routes (36 events in total, incl. `/health`), `PUT` in CORS, stage throttling 25 rps / burst 50 |
| ComplaintsTable | DynamoDB | `CivicTrack-Complaints` (PK `complaintId`, on-demand, SSE) | Complaint records | + GSI `citizenSub-createdAt-index` (online add, Replacement: False); Retain policies; new attributes are additive |
| EvidenceBucket | S3 | `EvidenceBucketName` stack output | Private evidence/proof photos | + lifecycle rule (abort incomplete multipart uploads after 1 day); Retain policies; new `progress/` key prefix |
| ComplaintsExecutionRole | IAM role | `CivicTrack-Lambda-Execution-Role` | Lambda permissions | − `dynamodb:DeleteItem`; + config-table access; Cognito actions now scoped to this pool ARN (was `userpool/*`) and extended with `AdminGetUser`, `AdminListGroupsForUser`, `AdminCreateUser`, `AdminAdd/RemoveUserFromGroup`, `AdminEnable/DisableUser` |
| ComplaintsFunction | Lambda | `CivicTrack-Complaints` (python3.14, 512 MB, 60 s) | API + SLA sweep | new code (6 modules); env vars `VIEW_URL_TTL_SECONDS`, `CONFIG_TABLE`, `CITIZEN_INDEX` |
| SlaScheduleRule | EventBridge rule | `CivicTrack-SLA-Checker`, `rate(15 minutes)` | SLA sweep trigger | none |
| SlaScheduleInvokePermission | Lambda permission | — | Allows EventBridge to invoke | none |
| CivicTrackPostConfirmationRole | IAM role | `CivicTrack-PostConfirmation-Role` | Add confirmed users to Citizen | none |
| CivicTrackPostConfirmationFunction | Lambda | `CivicTrack-PostConfirmation` (128 MB, 10 s) | Cognito trigger | none |
| CivicTrackPostConfirmationInvokePermission | Lambda permission | — | Allows Cognito to invoke | none |
| **ConfigTable** (new) | DynamoDB | `CivicTrack-Config` (PK `pk`, SK `sk`, on-demand, SSE, Retain) | Departments, categories, zones/wards + boundaries, officer profiles, settings, audit log | **new resource** (additive; output `ConfigTableName`) |
| ComplaintsFunction…Permission (×28) | Lambda permission | — | API Gateway → Lambda for new routes | new |

Outside the template:

| Resource | Notes |
|---|---|
| `/aws/lambda/CivicTrack-Complaints` log group | Created implicitly by Lambda. Retention **never expire**; ~454 KB stored. |
| `/aws/lambda/CivicTrack-PostConfirmation` log group | Same; ~0.5 KB. |
| `civictrack-sam-artifacts-…-ap-south-1` S3 bucket | SAM deployment artifacts (`resolve_s3`). |
| SES identity | Sender `SesSenderEmail` parameter. Account in **sandbox**, 200 emails / 24 h. |

## Stack parameters (current deployed values)

| Parameter | Value | Note |
|---|---|---|
| AllowedOrigin | `*` | Restrict to the hosted frontend origin once it has a stable URL |
| SesSenderEmail / AdminNotificationEmail | owner's mailbox | |
| SlaWarningHours | 4 | |

## Data stored (live, read-only check)

6 complaints: 1 `PENDING` unassigned, 1 `PENDING` assigned (legacy → shown as `ASSIGNED`), 4 `RESOLVED`. No coordinates, zones or wards yet.
