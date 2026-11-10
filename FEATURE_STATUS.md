# CivicTrack — Feature Status

Updated 2026-11-10 (platform-completion iteration, final verification). **Local** = implemented in the repository and verified by automated tests and/or a browser run against the local stack. **Live** = running in the deployed `civictrack-dev` stack. **Nothing from this iteration is live until the owner approves a deployment** (`DEPLOYMENT_GUIDE.md`).

Legend: ✅ done · 🟡 partial / limited · ⛔ not done (reason given)

## Citizen portal
| Feature | Local | Notes |
|---|---|---|
| Registration, email verification, login, logout | ✅ | Cognito via Amplify; labelled, autocompleting form fields |
| Password reset (forgot password) | ✅ | `resetPassword` → `confirmResetPassword`; same message whether or not the account exists |
| Profile: edit display name, change password | ✅ | Cognito `updateUserAttributes` / `updatePassword`; client-side check mirrors pool policy |
| Secure session handling | ✅ | Amplify token refresh; automatic sign-out after 30 min inactivity |
| Complaint registration: title, description, category, urgency | ✅ | Categories come from managed reference data |
| Automatic department routing | ✅ | Category → department mapping, shown before submit |
| GPS location, map pin, place search, address, landmark | ✅ | Browser geolocation; OSM Nominatim search (user-initiated) |
| Automatic ward/zone detection | ✅ | Point-in-polygon against **imported** boundaries only |
| Photo evidence upload | ✅ | Presigned S3 PUT; type/size verified client- and server-side |
| Duplicate warning and category suggestion before submit | ✅ | Rule-based; other citizens' content never revealed |
| Duplicate-submit protection; per-citizen rate limit | ✅ | `clientRequestId`; 10/hour (configurable) |
| Tracking: status, department, officer (configurable disclosure), timeline, SLA, progress %, before/progress/after photos, resolution note, rejection reason | ✅ | |
| Complaint history, search, filters, map view, map-click reporting | ✅ | |
| Feedback: star rating + comment on close; reopen within 14 days, max 3 times | ✅ | |
| Notification centre + email | ✅ | Feed from persisted history; email via SES (sandbox limits apply) |

## Officer portal
| Feature | Local | Notes |
|---|---|---|
| Profile: department, zones, wards, availability, workload cap | ✅ | Set by admin; read via `/me` |
| Assigned list, priority, SLA countdown, overdue alerts, daily workload | ✅ | |
| Accept assignment; decline with mandatory reason (≥10 chars) | ✅ | Decline returns complaint to Pending and notifies admins |
| Start work (optional before photo), progress updates (note, %, photo), resolve with mandatory proof | ✅ | |
| Map with category markers, directions link, nearest-next visit order | ✅ | Straight-line planning aid; **no live tracking** (position never leaves the browser) |
| Work history, avg resolution time, SLA %, citizen rating | ✅ | |
| Isolation from other officers' complaints | ✅ | Backend 403 on every route |

## Admin / authority portal
| Feature | Local | Notes |
|---|---|---|
| Search/filter by status, category, department, zone, ward, priority, SLA, date; CSV export | ✅ | |
| Assign / reassign (ranked suggestions with reasons) / unassign | ✅ | Unavailable officers require explicit override |
| Change priority, department, zone, ward | ✅ | Managed IDs; legacy free text still supported |
| Reject (reason, optional duplicate link); manual escalation | ✅ | |
| Evidence, progress and proof review; full history | ✅ | |
| Officer management: invite, profile, availability, workload, performance | ✅ | Cognito `AdminCreateUser`; profile in config table |
| User administration: list by role, change role, enable/disable | ✅ | Cannot modify own account; audited |
| Department management, category → department mapping | ✅ | |
| Zone and ward management, GeoJSON boundary import | ✅ | No boundary data shipped (see Limitations) |
| Configuration: SLA hours, warning hours, rate limit, officer-name disclosure | ✅ | |
| Audit log viewer | ✅ | Monthly partitions |
| Permission management beyond the three roles | 🟡 | Roles are Citizen/Officer/Admin (Cognito groups); finer-grained permissions not implemented |

## GIS
| Feature | Local | Notes |
|---|---|---|
| Interactive OSM map, category-specific symbols, status colours | ✅ | Leaflet + OpenStreetMap tiles (free) |
| Clustering, heatmap, hotspots (≥3 within ~500 m) | ✅ | Hotspots computed server-side |
| Ward/zone boundaries and density shading | ✅ | Requires imported GeoJSON |
| Filters: date, category, status, priority, department, zone, ward, SLA | ✅ | |
| Location search (places + complaint addresses), clickable markers, map-based registration | ✅ | |
| Official ward/zone boundary dataset | ⛔ | Not supplied; no boundaries fabricated |

## Analytics & reporting
| Feature | Local | Notes |
|---|---|---|
| Server-side aggregation (`/analytics`) | ✅ | Browser fallback if endpoint not deployed |
| Totals, new, pending, in progress, resolved, rejected, reopened, overdue | ✅ | |
| Resolution %, average and median resolution time, SLA compliance % | ✅ | |
| By department, officer, ward, zone, category, priority; daily/weekly/monthly trends | ✅ | |
| Heatmap and hotspots | ✅ | |
| Date/department/zone/ward/category/priority filters; CSV export | ✅ | |
| PDF report | 🟡 | Browser print-to-PDF with a print stylesheet; no server-generated PDF |

## Automation & intelligence
| Feature | Local | Notes |
|---|---|---|
| 48 h SLA, configurable warning window, 15-min schedule | ✅ | |
| Idempotent warning, breach escalation, level-2 escalation (24 h overdue) | ✅ | Claim-then-send; tested under duplicate delivery |
| Category suggestion, priority scoring, duplicate/similar detection, routing, summary | ✅ | **Rule-based** (keywords, weights, distance); no ML model or AI service |

## Platform
| Feature | Local | Notes |
|---|---|---|
| Consistent error shape `{error, code}`, validation, status codes, pagination | ✅ | |
| Citizen GSI query (with scan fallback), conditional writes, idempotency, audit | ✅ | GSI requires deploy |
| Structured JSON logging | ✅ | No emails/tokens/free text logged |
| API throttling (25 rps, burst 50) | ✅ | Template only, needs deploy |
| `DeletionPolicy: Retain` on data resources | ✅ | Template only |
| Automated tests | ✅ | 221 backend + 65 frontend, all passing (re-run 2026-11-10) |
| Documentation (architecture, API, schema, security, UML, user manual, testing, deployment, cost, IEEE draft) | ✅ | 13 Mermaid diagrams parse-checked; links checked |

## Not done / blocked
| Item | Blocker |
|---|---|
| Deployment of this iteration | Owner approval (`DEPLOYMENT_GUIDE.md`) |
| Official ward/zone boundaries | Dataset + licence from owner (e.g. BBMP/GBA ward GeoJSON) |
| SES production access | AWS request by owner; until then only verified recipients get email |
| Live officer GPS tracking | Not implemented by design (consent, privacy, cost) |
| Server-generated PDF reports | Browser print used instead |
| Load testing on AWS | Requires deployed environment |
| IEEE paper | 🟡 Draft written (`docs/IEEE_PAPER.md`) with 6 verified references and local/emulator results only; needs the official IEEE template, author details and AWS measurements |
| Commit of this iteration | Owner approval (working tree is uncommitted) |
