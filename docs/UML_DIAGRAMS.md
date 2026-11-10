# CivicTrack UML & Architecture Diagrams

Mermaid diagrams (render on GitHub, VS Code or mermaid.live), reflecting the code on 2026-11-10. Identifiers match `backend/complaints/*.py`, `infrastructure/template.yaml` and `frontend/src`. The ER/data-model diagram is in `DATABASE_SCHEMA.md`.

## 1. Use case diagram

```mermaid
flowchart LR
  Citizen((Citizen))
  Officer((Field Officer))
  Admin((Administrator))
  Sched((EventBridge<br/>15-min schedule))
  subgraph CivicTrack
    C1([Register · verify · sign in · reset password])
    C2([Edit profile · change password])
    C3([Report complaint: category, urgency, GPS/map pin, photo])
    C4([See duplicate warning & category suggestion])
    C5([Track: timeline, SLA, progress, proof photos])
    C6([Close with rating/feedback or reopen])
    O1([Accept or decline assignment])
    O2([Start work · progress update · resolve with proof])
    O3([Field map · directions · visit order])
    A1([Triage: classify, assign/reassign/unassign])
    A2([Reject or escalate complaint])
    A3([Manage departments & category routing])
    A4([Manage zones & wards · import GeoJSON])
    A5([Invite staff · change roles · enable/disable])
    A6([Officer profiles: department, areas, availability])
    A7([Analytics, reports, CSV/PDF export])
    A8([Settings · audit log])
    S1([SLA warning · breach escalation · level-2 escalation])
    N([Email + notification centre])
  end
  Citizen --- C1 & C2 & C3 & C4 & C5 & C6
  Officer --- C1 & O1 & O2 & O3
  Admin --- C1 & A1 & A2 & A3 & A4 & A5 & A6 & A7 & A8
  Sched --- S1
  C3 -.include.-> C4
  C3 & A1 & O1 & O2 & C6 & A2 & S1 -.include.-> N
```

## 2. Class diagram (domain and backend modules)

```mermaid
classDiagram
  class Complaint {
    +String complaintId
    +String title, category, location, description, summary
    +Decimal latitude, longitude
    +String status
    +String priority
    +int priorityScore
    +String departmentId, wardId, zoneId
    +String citizenSub, assignedOfficerSub
    +String createdAt, slaStartedAt, acceptedAt
    +int slaHours, reopenCount, percentComplete, escalationLevel
    +bool escalated
    +HistoryEntry[] history
  }
  class HistoryEntry {
    +String at, action, actorRole, actorSub
    +String fromStatus, toStatus, note
  }
  class Department {
    +String id
    +String name
    +String description
    +bool active
  }
  class Category {
    +String name
    +String departmentId
    +bool active
  }
  class Zone {
    +String id
    +String name
    +Geometry boundary
  }
  class Ward {
    +String id
    +String name
    +String number
    +String zoneId
    +Geometry boundary
  }
  class OfficerProfile {
    +String departmentId
    +String[] zoneIds
    +String[] wardIds
    +bool available
    +int maxActive
  }
  class Settings {
    +int slaHours
    +int warningHours
    +int maxComplaintsPerHour
    +bool discloseOfficerName
  }
  class AuditEntry {
    +String at
    +String actorSub
    +String actorRole
    +String action
    +String target
    +String details
  }
  class Status {
    <<enumeration>>
    PENDING
    ASSIGNED
    IN_PROGRESS
    RESOLVED
    REOPENED
    CLOSED
    REJECTED
  }
  class lifecycle {
    <<module>>
    +effective_status(item)
    +can_transition(action, status)
    +sla_state(item, now, warning_hours)
    +department_for(item)
  }
  class intelligence {
    <<module>>
    +suggest_category(title, description)
    +score_priority(category, title, description, nearby_open, urgency)
    +summarize(title, description)
  }
  class geo {
    <<module>>
    +validate_geometry(geometry)
    +point_in_geometry(lng, lat, geometry, bbox)
    +locate(lng, lat, areas)
    +grid_hotspots(points)
  }
  class analytics_engine {
    <<module>>
    +aggregate(items, now, filters, granularity, warning_hours)
  }
  class reference {
    <<module>>
    +load(table)
    +route_category(ref, category)
    +write_audit(table, user, action, target, details)
  }
  class app {
    <<Lambda handler>>
    +lambda_handler(event, context)
    +authenticate_request(event)
    +create_complaint / check_similar / get_complaints
    +assign / unassign / reject / escalate
    +update_officer_workflow(action)
    +reopen / close
    +admin handlers (users, reference, boundaries, settings, audit)
    +get_analytics / get_notifications / get_me
    +process_sla_checks(now)
  }
  Complaint "1" *-- "0..200" HistoryEntry
  Complaint --> Status
  Complaint --> Department
  Complaint --> Ward
  Complaint --> Zone
  Category --> Department
  Ward --> Zone
  OfficerProfile --> Department
  app ..> lifecycle
  app ..> intelligence
  app ..> geo
  app ..> analytics_engine
  app ..> reference
  analytics_engine ..> geo
  reference ..> Department
  reference ..> Settings
  reference ..> AuditEntry
```

## 3. Sequence — citizen submits a complaint

```mermaid
sequenceDiagram
  actor C as Citizen
  participant SPA as React SPA
  participant L as Lambda (via API Gateway)
  participant S3
  participant DB as DynamoDB
  participant CFG as Config table
  participant SES
  C->>SPA: fill form, pin location, choose photo
  SPA->>SPA: validateComplaintForm()
  SPA->>L: POST /complaints/similar
  L->>DB: scan open items with coordinates
  L-->>SPA: duplicates (distance/status only), category suggestion
  SPA-->>C: warning panel (submit anyway / change category)
  SPA->>L: POST /evidence/upload-url
  L-->>SPA: presigned PUT (300 s)
  SPA->>S3: PUT photo
  SPA->>L: POST /complaints {…, photoKey, clientRequestId}
  L->>CFG: load reference (cached 60 s)
  L->>DB: GetItem(idempotency) · Query GSI (hourly rate limit)
  L->>S3: HeadObject (type, size)
  L->>L: triage: route department, score priority, locate ward/zone
  L->>DB: PutItem IF attribute_not_exists
  L->>SES: citizen + admin emails
  L-->>SPA: 201 complaint (tracking ID)
```

## 4. Sequence — assignment and field work

```mermaid
sequenceDiagram
  actor A as Admin
  actor O as Officer
  participant L as Lambda
  participant COG as Cognito
  participant DB as DynamoDB
  participant S3
  A->>L: GET /officers
  L->>COG: ListUsersInGroup(Officer)
  L-->>A: directory + profiles (ranked in UI)
  A->>L: PATCH /complaints/{id}/assign {officerSub}
  L->>COG: verify enabled Officer
  L->>DB: conditional update → ASSIGNED (+history, audit)
  O->>L: PATCH /accept (or /decline with reason → PENDING)
  O->>L: PATCH /start {beforePhotoKey?}
  O->>L: PATCH /progress {note, percentComplete, photoKey?}
  O->>L: POST /proof-upload-url {stage: after}
  O->>S3: PUT proof photo
  O->>L: PATCH /resolve {proofPhotoKey, note}
  L->>S3: HeadObject
  L->>DB: conditional update → RESOLVED
  L-->>O: 200 (citizen + admin emailed)
```

## 5. Sequence — SLA sweep (idempotent)

```mermaid
sequenceDiagram
  participant EB as EventBridge
  participant L as Lambda
  participant DB as DynamoDB
  participant SES
  EB->>L: Scheduled Event (every 15 min)
  L->>DB: Scan status IN (PENDING, ASSIGNED, IN_PROGRESS, REOPENED)
  loop each complaint (errors isolated per record)
    alt deadline passed
      L->>DB: SET escalated IF not escalated (+history)
      alt breach not yet notified
        L->>DB: claim escalationNotifiedAt IF not exists
      else 24 h past deadline
        L->>DB: claim escalationLevel2NotifiedAt IF not exists
      end
    else inside warning window
      L->>DB: claim slaWarningSentAt IF not exists
    end
    opt claim won
      L->>SES: email
      alt send failed
        L->>DB: REMOVE flag IF flag = our stamp
      end
    end
  end
```

## 6. Activity diagram — complaint lifecycle

```mermaid
flowchart TD
  S([Citizen submits]) --> P[PENDING]
  P -- admin rejects with reason --> RJ[REJECTED]
  P -- admin assigns --> AS[ASSIGNED]
  AS -- reassign before work --> AS
  AS -- admin unassigns --> P
  AS -- officer declines with reason --> P
  AS -- admin rejects --> RJ
  AS -- officer accepts --> AS
  AS -- officer starts --> IP[IN_PROGRESS]
  IP -- progress update --> IP
  IP -- proof photo + resolve --> R[RESOLVED]
  R -- citizen confirms or admin closes --> CL[CLOSED]
  R -- citizen reopens within 14 days, max 3 --> RO[REOPENED<br/>new SLA period]
  RO -- assigned officer restarts --> IP
  RO -- admin reassigns --> AS
  RO -- admin rejects --> RJ
  P & AS & IP & RO -. every 15 min .-> SLA{SLA}
  SLA -- warning window --> W[warning email once]
  SLA -- past deadline --> E[escalate + email once]
  SLA -- 24 h past deadline --> E2[level-2 email once]
```

## 7. Deployment diagram

```mermaid
flowchart TB
  subgraph Client["Browser"]
    SPA[React 19 SPA<br/>static build]
  end
  subgraph AWS["AWS ap-south-1 · stack civictrack-dev"]
    COG[Cognito user pool<br/>Citizen / Officer / Admin]
    APIGW[API Gateway REST /dev<br/>Cognito authorizer · 25 rps throttle]
    LC[Lambda CivicTrack-Complaints<br/>python3.14 · 512 MB]
    LP[Lambda CivicTrack-PostConfirmation]
    EB[EventBridge CivicTrack-SLA-Checker]
    DDB[(DynamoDB CivicTrack-Complaints<br/>+ citizenSub GSI)]
    CFG[(DynamoDB CivicTrack-Config)]
    S3[(S3 evidence bucket, private)]
    SES[SES]
    CW[CloudWatch Logs]
  end
  OSM[OpenStreetMap tiles + Nominatim]
  SPA --> COG
  SPA --> APIGW --> LC
  SPA -- presigned URLs --> S3
  SPA --> OSM
  COG --> LP
  EB --> LC
  LC --> DDB & CFG & S3 & SES & COG & CW
```

The config table, citizen GSI, new routes and throttling are defined in `infrastructure/template.yaml` but **not yet deployed** (see `DEPLOYMENT_GUIDE.md` §3).

## 8. Component diagram (frontend)

```mermaid
flowchart TB
  main[main.jsx] --> AP[AuthProvider<br/>session + idle sign-out] --> App[App.jsx / PortalRouter]
  App --> FB[FeedbackProvider: toasts, confirm]
  App --> Login[Login: sign in, register, verify, reset]
  App -. lazy .-> CP[CitizenPortal]
  App -. lazy .-> OD[OfficerDashboard]
  App -. lazy .-> AD[AdminDashboard]
  AD --> AV[AnalyticsView]
  AD --> MV[ManagementViews: departments, areas, officers, users, settings, audit]
  CP & OD & AD --> Shell[PortalShell + notification centre]
  CP & OD & AD --> Det[ComplaintDetail drawer]
  CP & OD & AD --> Map[ComplaintMap · LocationPicker · PlaceSearch]
  CP & OD & AD --> Data[data.js: useReference, useNotifications]
  CP & OD & AD --> CC[complaints.jsx: useComplaints, FilterBar, tables]
  Data & CC & Det --> API[lib/api.js]
  AD --> ASG[lib/assignment.js rankOfficers]
  AV --> AN[lib/analytics.js]
  CP --> VAL[lib/validation.js]
  Map --> GEO[lib/geo.js]
```

## 9. Data flow diagrams

### Level 0

```mermaid
flowchart LR
  Citizen -- complaint, photo, feedback --> CT((CivicTrack))
  CT -- status, proof, emails --> Citizen
  Admin -- assignments, classification, configuration, users --> CT
  CT -- dashboards, reports, audit, alerts --> Admin
  Officer -- responses, progress, proof --> CT
  CT -- assignments, SLA alerts --> Officer
  OSM[(OpenStreetMap)] -- tiles, geocoding --> CT
```

### Level 1

```mermaid
flowchart LR
  C[Citizen] -->|complaint| P1((1 Register & triage))
  P1 <--> D1[(Complaints)]
  P1 <--> D4[(Config: categories, wards, settings)]
  C -->|photo| P2((2 Evidence))
  P2 <--> D2[(S3)]
  A[Admin] -->|assign, reject, escalate| P3((3 Triage & assign))
  P3 <--> D1
  P3 <-->|Officer group| D3[(Cognito)]
  A -->|departments, areas, GeoJSON, users, settings| P8((8 Administration))
  P8 <--> D4
  P8 <--> D3
  O[Officer] -->|accept, progress, resolve| P4((4 Field work))
  P4 <--> D1
  O -->|photos| P2
  P5((5 SLA monitoring)) <--> D1
  P1 & P3 & P4 & P5 -->|events| P6((6 Notify))
  P6 -->|email| C & O & A
  D1 --> P7((7 Analytics & notification feed))
  P7 -->|charts, CSV, feed| A & C & O
  P3 & P8 -->|audit| D4
```
