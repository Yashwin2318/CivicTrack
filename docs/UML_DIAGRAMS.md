# CivicTrack UML & Architecture Diagrams

All diagrams are Mermaid (render on GitHub, VS Code, or mermaid.live) and reflect the code as of 2026-11-09. Names match identifiers in `backend/complaints/app.py`, `lifecycle.py` and `frontend/src`.

## 1. Use case diagram

```mermaid
flowchart LR
  Citizen((Citizen))
  Officer((Field Officer))
  Admin((Administrator))
  Sched((EventBridge<br/>scheduler))
  subgraph CivicTrack
    UC1([Register / sign in])
    UC2([Report complaint with location & photo])
    UC3([Track complaint & view timeline])
    UC4([View resolution proof])
    UC5([Reopen or close resolved complaint])
    UC6([Assign / reassign / unassign officer])
    UC7([Set priority, department, zone, ward])
    UC8([View map, analytics, export CSV])
    UC9([View officer workload])
    UC10([Start work, before photo])
    UC11([Resolve with proof photo])
    UC12([Plan visit order, get directions])
    UC13([Check SLA, warn, escalate])
    UC14([Send email notification])
  end
  Citizen --- UC1 & UC2 & UC3 & UC4 & UC5
  Admin --- UC1 & UC6 & UC7 & UC8 & UC9 & UC4
  Officer --- UC1 & UC10 & UC11 & UC12
  Sched --- UC13
  UC2 -.include.-> UC14
  UC6 -.include.-> UC14
  UC11 -.include.-> UC14
  UC13 -.include.-> UC14
```

## 2. Class diagram (domain + backend modules)

```mermaid
classDiagram
  class Complaint {
    +String complaintId
    +String title
    +String category
    +String location
    +String description
    +Decimal latitude
    +Decimal longitude
    +String status
    +String priority
    +String department
    +String zone
    +String ward
    +String citizenSub
    +String assignedOfficerSub
    +String createdAt
    +String slaStartedAt
    +int slaHours
    +bool escalated
    +int reopenCount
    +HistoryEntry[] history
    +NotificationStatus lastCitizenNotification
  }
  class HistoryEntry {
    +String at
    +String action
    +String actorRole
    +String actorSub
    +String fromStatus
    +String toStatus
    +String note
  }
  class User {
    +String sub
    +String email
    +Role role
  }
  class Role {
    <<enumeration>>
    Citizen
    Officer
    Admin
  }
  class Status {
    <<enumeration>>
    PENDING
    ASSIGNED
    IN_PROGRESS
    RESOLVED
    REOPENED
    CLOSED
  }
  class lifecycle {
    <<module>>
    +effective_status(item) Status
    +can_transition(action, status) bool
    +department_for(item) String
    +sla_state(item, now, warning_hours) dict
    +parse_iso_datetime(value) datetime
  }
  class app {
    <<module>>
    +lambda_handler(event, context)
    +authenticate_request(event) User
    +create_complaint(event, user)
    +get_complaints(user)
    +assign_complaint(event, user, id)
    +unassign_complaint(event, user, id)
    +update_officer_workflow(event, user, id, action)
    +reopen_complaint(event, user, id)
    +close_complaint(event, user, id)
    +apply_transition(table, item, fields, entry)
    +process_sla_checks(now)
    +send_notification(recipients, subject, body)
  }
  Complaint "1" *-- "0..200" HistoryEntry
  Complaint --> Status
  User --> Role
  User "1" --> "0..*" Complaint : reports (Citizen)
  User "1" --> "0..*" Complaint : handles (Officer)
  app ..> lifecycle : uses
  app ..> Complaint : persists
```

## 3. Sequence — citizen submits a complaint with photo

```mermaid
sequenceDiagram
  actor C as Citizen
  participant SPA as React SPA
  participant GW as API Gateway
  participant L as Lambda
  participant S3
  participant DB as DynamoDB
  participant SES
  C->>SPA: fill form, pin location, choose photo
  SPA->>SPA: validateComplaintForm()
  SPA->>GW: POST /evidence/upload-url (JWT)
  GW->>L: claims
  L-->>SPA: uploadUrl, photoKey (300 s)
  SPA->>S3: PUT photo (presigned)
  SPA->>GW: POST /complaints {…, photoKey, clientRequestId}
  GW->>L: claims
  L->>S3: HeadObject(photoKey) — type & size
  L->>DB: PutItem (attribute_not_exists)
  L->>SES: citizen + admin emails
  L->>DB: SET lastCitizenNotification
  L-->>SPA: 201 complaint
  SPA-->>C: toast with tracking ID
```

## 4. Sequence — assignment, work and resolution

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
  L-->>A: directory
  A->>L: PATCH /complaints/{id}/assign {officerSub}
  L->>COG: verify enabled Officer member
  L->>DB: GetItem → check transition
  L->>DB: UpdateItem IF status & officer unchanged (+history)
  L-->>A: 200 ASSIGNED
  O->>L: PATCH /start {note, beforePhotoKey?}
  L->>DB: conditional update → IN_PROGRESS
  O->>L: POST /proof-upload-url {stage: after}
  L-->>O: presigned PUT
  O->>S3: PUT proof photo
  O->>L: PATCH /resolve {proofPhotoKey, note}
  L->>S3: HeadObject
  L->>DB: conditional update → RESOLVED
  L-->>O: 200 (citizen + admin emailed)
```

## 5. Sequence — SLA sweep (idempotent)

```mermaid
sequenceDiagram
  participant EB as EventBridge (15 min)
  participant L as Lambda
  participant DB as DynamoDB
  participant SES
  EB->>L: Scheduled Event
  L->>DB: Scan status IN (open statuses)
  loop each complaint
    alt past deadline
      L->>DB: SET escalated IF not escalated (+history)
      L->>DB: SET escalationNotifiedAt IF not exists (claim)
    else in warning window
      L->>DB: SET slaWarningSentAt IF not exists (claim)
    end
    opt claim won
      L->>SES: email officer + admin
      alt send failed
        L->>DB: REMOVE flag IF flag = our stamp (retry next run)
      end
    end
  end
  L-->>EB: summary {scanned, warningsSent, newEscalations, errors}
```

## 6. Activity diagram — complaint lifecycle

```mermaid
flowchart TD
  S([Citizen submits]) --> P[PENDING]
  P --> A{Admin assigns?}
  A -- yes --> AS[ASSIGNED]
  AS -- reassign --> AS
  AS -- unassign --> P
  AS --> ST[Officer starts work<br/>optional before photo]
  ST --> IP[IN_PROGRESS]
  IP --> PR[Upload after photo] --> R[RESOLVED]
  R --> Q{Citizen satisfied?}
  Q -- yes --> CL[CLOSED]
  Q -- no, reason --> RO[REOPENED<br/>new SLA period]
  RO --> A2{Same officer?}
  A2 -- restart --> IP
  A2 -- reassign --> AS
  R -. admin verifies .-> CL
  P & AS & IP & RO -. every 15 min .-> SLA{SLA check}
  SLA -- final 4 h --> W[Warning email once]
  SLA -- past 48 h --> E[Escalate + email once]
```

## 7. Deployment diagram

```mermaid
flowchart TB
  subgraph Client["Client devices"]
    BR[Browser: React 19 SPA<br/>static build]
  end
  subgraph AWS["AWS ap-south-1 — stack civictrack-dev"]
    COG[Cognito User Pool<br/>CivicTrack-Users]
    APIGW[API Gateway REST<br/>CivicTrack-API / dev]
    subgraph Lambda
      LC[CivicTrack-Complaints<br/>python3.14 · 512 MB]
      LP[CivicTrack-PostConfirmation<br/>128 MB]
    end
    EB[EventBridge rule<br/>CivicTrack-SLA-Checker]
    DDB[(DynamoDB<br/>CivicTrack-Complaints)]
    S3[(S3 evidence bucket)]
    SES[SES]
    CW[CloudWatch Logs]
  end
  OSM[OpenStreetMap tile servers]
  BR --> COG
  BR --> APIGW --> LC
  BR --> S3
  BR --> OSM
  COG --> LP
  EB --> LC
  LC --> DDB & S3 & SES & COG & CW
```

## 8. Component diagram (frontend)

```mermaid
flowchart TB
  main[main.jsx] --> AP[AuthProvider] --> App[App.jsx / PortalRouter]
  App --> FB[FeedbackProvider<br/>toasts + confirm]
  App --> Login
  App -. lazy .-> CP[CitizenPortal]
  App -. lazy .-> OD[OfficerDashboard]
  App -. lazy .-> AD[AdminDashboard]
  CP & OD & AD --> Shell[PortalShell]
  CP & OD & AD --> Det[ComplaintDetail]
  CP & OD & AD --> CC[complaints.jsx<br/>useComplaints, FilterBar, ComplaintTable]
  AD & OD --> Map[ComplaintMap<br/>react-leaflet]
  AD --> Charts
  CC & Det --> API[lib/api.js]
  CC --> LC[lib/lifecycle.js]
  AD --> AN[lib/analytics.js]
  OD & Det --> GEO[lib/geo.js]
  CP --> VAL[lib/validation.js]
```

## 9. Data flow diagrams

### Level 0 (context)

```mermaid
flowchart LR
  Citizen -- complaint, photo, feedback --> CT((CivicTrack))
  CT -- status, proof, emails --> Citizen
  Admin -- assignments, classification --> CT
  CT -- dashboards, reports, alerts --> Admin
  Officer -- progress, proof photos --> CT
  CT -- assignments, SLA alerts --> Officer
```

### Level 1

```mermaid
flowchart LR
  C[Citizen] -->|complaint data| P1((1 Register complaint))
  C -->|photo| P2((2 Evidence storage))
  P2 --> D2[(S3 evidence)]
  P1 --> D1[(Complaints table)]
  A[Admin] -->|officer, priority, area| P3((3 Triage & assign))
  P3 <--> D1
  P3 <-->|Officer group| D3[(Cognito)]
  O[Officer] -->|start / resolve| P4((4 Field work))
  P4 <--> D1
  O -->|proof| P2
  P5((5 SLA monitoring)) <--> D1
  P1 & P3 & P4 & P5 -->|events| P6((6 Notify))
  P6 -->|email| C & O & A
  D1 --> P7((7 Analytics & export))
  P7 -->|charts, CSV| A
```
