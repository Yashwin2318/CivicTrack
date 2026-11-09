# CivicTrack Database Schema

Table **`CivicTrack-Complaints`** — DynamoDB, on-demand, SSE enabled, PITR disabled.

| Key | Type |
|---|---|
| Partition key `complaintId` | String, `CT-` + 10 uppercase hex characters |

No sort key, no GSIs (see `AWS_BUDGET_AUDIT.md` B1 for the proposed indexes).

## Attributes

DynamoDB is schemaless; this is the contract enforced by `backend/complaints/app.py`. **Bold** = added in this work (additive; old items remain valid).

| Attribute | Type | Written by | Notes |
|---|---|---|---|
| complaintId | S | create | |
| title, category, location, description | S | create | length limits 150/60/250/3000 |
| **latitude, longitude** | N | create | optional, 6 dp |
| **locationAccuracy** | N | create | metres |
| **landmark** | S | create | |
| photoKey | S | create | `citizens/{sub}/complaint-evidence/{uuid}.{ext}` or "" |
| citizenSub, citizenEmail | S | create | from verified JWT |
| status | S | lifecycle | PENDING, **ASSIGNED**, IN_PROGRESS, RESOLVED, **REOPENED**, **CLOSED** |
| priority | S | create / admin | LOW, MEDIUM, HIGH, CRITICAL |
| **department** | S | create / admin | derived from category; admin may override |
| **zone, ward** | S | admin | free text ≤80 |
| assignedOfficerSub, assignedOfficerName | S | assign / unassign | "" when unassigned |
| assignedAt, assignedBySub | S | assign | |
| **unassignedAt** | S | unassign | |
| workStartedAt | S | start | |
| **beforePhotoKey** | S | start | `officers/{sub}/complaints/{id}/before/…` |
| resolvedAt, resolvedBySub, proofPhotoKey | S | resolve | proof under `…/proof/` |
| **resolutionNote** | S | resolve | |
| **reopenedAt, reopenCount** | S, N | reopen | |
| **slaStartedAt** | S | reopen | SLA clock restarts here |
| **closedAt, closedByRole, citizenRating** | S, S, N | close | |
| createdAt | S | create | ISO-8601 UTC |
| slaHours | N | create | 48 |
| escalated, escalatedAt | BOOL, S | SLA sweep | |
| slaWarningSentAt, escalationNotifiedAt | S | SLA sweep | one-time claim flags |
| **lastCitizenNotification** | M | notifications | `{kind, sent, at}` |
| **history** | L of M | every action | `{at, action, actorRole, actorSub, fromStatus, toStatus, note?, officerName?, previousOfficerName?}`; capped at 200 |
| **updatedAt** | S | admin details | |

## Status mapping for legacy items

| Stored | assignedOfficerSub | Effective |
|---|---|---|
| PENDING | "" / missing | PENDING |
| PENDING | non-empty | ASSIGNED |
| others | — | unchanged |

## Access patterns

| Pattern | Current implementation | Proposed (needs deploy) |
|---|---|---|
| Citizen's complaints | Scan + filter `citizenSub` | Query GSI `citizenSub-createdAt` |
| Officer's complaints | Scan + filter `assignedOfficerSub` | Query GSI `assignedOfficerSub-createdAt` |
| All complaints (admin) | Scan | Scan with pagination |
| SLA sweep | Scan + filter open statuses | Query sparse GSI on `openSla` attribute |
| Single complaint | GetItem (consistent) | same |

## Object storage layout (private S3 bucket)

```
citizens/{citizenSub}/complaint-evidence/{uuid}.{jpg|png|webp}
officers/{officerSub}/complaints/{complaintId}/before/{uuid}.{ext}
officers/{officerSub}/complaints/{complaintId}/proof/{uuid}.{ext}
```
Keys are generated server-side; clients cannot choose a path.
