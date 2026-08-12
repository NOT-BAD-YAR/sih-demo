# SIH25244 — User and Entity Behaviour Analytics (UEBA) for Internal Threat Identification — Build Plan

## 1. Overview

We are building a **UEBA (User and Entity Behaviour Analytics)** platform that continuously learns the normal behaviour of every **User, Device, Server, and Application** inside an organization, then detects suspicious deviations in real time.

The platform replaces signature-based detection (which only catches known threats) with a **behavioural baseline + machine-learning anomaly engine**. A single event may look legitimate in isolation; risk only becomes visible when an event is compared against the entity's own normal baseline and its surrounding context.

### The 5 canonical anomalies the engine must detect

1. **Volume spike** — an employee normally downloads 20 MB/day, suddenly downloads 5 GB.
2. **Impossible travel** — the account logs in from two geographically distant locations within a short time.
3. **Access outside scope** — an account accesses files outside its usual department.
4. **Dormant account activation** — an account that has been idle suddenly becomes active at an unusual hour.
5. **Novel peer** — a server suddenly communicates with a device it has never contacted before.

### Excluded for now

- **Blockchain audit layer** — explicitly dropped for the current build. The evidence trail will be stored in PostgreSQL instead of a tamper-evident ledger. It can be added back later as a standalone phase.

---

## 2. Locked Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Streaming | **Apache Kafka (real, not simulated)** | Real-time pipeline; decouples collecting, processing, and alerting |
| ML model | **Isolation Forest (unsupervised) + heuristic rule engine** | No labeled attack data needed; scoring is cheap enough for real time |
| Event sources | **Real device agents** (Windows primary) + **Event Simulator** for the demo | Proves the real-time loop with live data while keeping demo deterministic |
| Dashboard | **Multi-user with roles** (Analyst / Admin) | SOC teams work as a team; incidents need assignment and review |
| Storage | PostgreSQL | Relational storage for events, profiles, alerts, incidents |
| Languages | Python (agents + analytics + API) · React + TypeScript (dashboard) | Matches modern UEBA stacks and demo velocity |

---

## 3. Architecture (full view)

```
┌────────────────────────────────────────────────────────────────────┐
│                       ENTERPRISE ENVIRONMENT                       │
│                                                                    │
│   Users      Devices      Servers        Apps          IAM         │
│     └─────────────┬───────────────────────────┬─────────────┘     │
┌────────────────────▼─────────────────────────┬───────────────────────────┐
│ 1. EVENT COLLECTION (AGENTS)                 │  Event Simulator (demo)    │
│    Windows Agent (Security log, Sysmon,      │  Generates realistic org   │
│    file events, USB, process)                │  activity + planted        │
│    Linux Agent (auditd/journald)  [later]    │  anomalies (all 5 cases)   │
└────────────────────────────┬──────────────────────────────────────────────┘
                             │ normalized JSON events
                             ▼
┌────────────────────────────────────────────────────────────────────┐
│ 2. EVENT STREAMING — APACHE KAFKA                                  │
│    topics: auth-events · file-events · network-events ·            │
│            device-events · privilege-events                        │
│    Producer  (agent/simulator → topic)                             │
│    Consumer  (analytics engine consumes, deduped, exactly-once     │
│               at-least-once delivery)                              │
└────────────────────────────────────────────────────────────────────┘
                             │
             ┌───────────────┴───────────────┐
             ▼                               ▼
┌─────────────────────────────┐   ┌──────────────────────────────────────────┐
│ 3. STORAGE — POSTGRESQL     │   │ 4. ANALYTICS ENGINE (Python consumer)     │
│   raw_events                │   │                                          │
│   users · devices · servers │   │ 4.1 Normalize & validate                 │
│   entities                  │   │ 4.2 Windowed feature engineering         │
│   behavioral_profiles       │   │ 4.3 Baseline builder (rolling window)    │
│   alerts · incidents        │   │ 4.4 Isolation Forest anomaly scoring     │
│   analyst_actions           │   │ 4.5 Heuristic rules (5 canonical cases)  │
└─────────────────────────────┘   │ 4.6 Context enrichment                   │
                                  │ 4.7 Risk score 0–100                    │
                                  │ 4.8 Correlation → incident timeline      │
                                  └──────────────────┬───────────────────────┘
                                                     │ risk-flagged behavior
                                                     ▼
                              ┌──────────────────────────────────────────────┐
                              │ 5. ALERT & INCIDENT ENGINE + RESPONSE ENGINE │
                              │   threshold → alert (low) / incident (high)  │
                              │   recommended + simulated actions:           │
                              │   force MFA · revoke session · restrict      │
                              │   access · isolate device                    │
                              └────────────────────────┬─────────────────────┘
                                                     │
                                                     ▼
                              ┌──────────────────────────────────────────────┐
                              │ 6. API (FastAPI, JWT auth, roles)            │
                              └────────────────────────┬─────────────────────┘
                                                     │
                                                     ▼
                              ┌──────────────────────────────────────────────┐
                              │ 7. SOC DASHBOARD (React + TypeScript)        │
                              │   multi-user · Analyst & Admin roles         │
                              │   risk overview · entity drill-down ·        │
                              │   alert/incident queue · timeline ·          │
                              │   explainability ("why flagged") ·           │
                              │   response actions                           │
                              └──────────────────────────────────────────────┘
```

**How the pieces tie together (one complete anomaly, end to end):**

```
Device agent ships login event    → Kafka (auth-events)
                                    → Analytics consumer
                                        → extract features vs that user's baseline
                                        → Isolation Forest picks dev iation
                                        → rule engine flags "unusual hour + new location"
                                        → context: admin role + sensitive folder → risk = 82
                                    → stored as alert (high) → escalated to incident
                                    → API exposes it
                                    → SOC analyst sees it live on dashboard
                                        → "why flagged" panel explains each signal
                                        → analyst clicks "Revoke session" (simulated)
                                        → action + analyst notes attached to incident
```

---

## 4. Data Model (PostgreSQL — core tables)

- `users` — organization employees (name, role, department, sensitivity tier, disabled/dormant flag)
- `entities` — devices, servers, apps (type, hostname, location)
- `raw_events` — every normalized event from Kafka (event_type, entity_id, ts, payload JSONB)
- `behavioral_profiles` — per-entity: feature baselines (avg/std of volume, hours hist, locations, peer set, dept-set, staleness) + model state
- `feature_windows` — hour-sized feature vectors used to train / score
- `alerts` — individual anomaly detections (severity, risk score, evidence refs, status)
- `incidents` — correlated cluster of alerts around one entity/timeline (assigned_to, status, notes)
- `users_accounts` — dashboard logins for analysts/admins (hashed password, role)
- `analyst_actions` — audit trail of what a responder did on an incident

---

## 5. Phases

### Phase 0 — Repository & Infrastructure Scaffolding
- Set up monorepo layout:
  ```
  insider-threat/
    agents/          # Windows (and later Linux) collection agents
    simulator/       # demo event generator
    streaming/       # kafka docker + topic bootstrap + schema
    analytics/       # consumer, features, baseline, ML, rules, risk
    api/             # FastAPI service
    dashboard/       # React + TypeScript app
    db/              # migrations + seed scripts
    tests/
    docs/
  ```
- `docker-compose.yml`: Kafka (KRaft, no ZooKeeper dependency), PostgreSQL, engine, api
- Config management (env files / `.env`), logging, health checks
- DB migrations skeleton (Alembic for Python)

**Exit criteria:** `docker compose up` starts Kafka + PostgreSQL cleanly; empty tables created.

### Phase 1 — Event Schema, Simulator, and Real Windows Agent
- Define the **Common Event Schema** (normalized fields shared by all sources):
  `event_id, timestamp, entity_type, entity_id, user_id, event_type,
   source, ip, geolocation, file_path, bytes_moved, peer_entity_id,
   outcome, severity, raw_payload`
- Build the **Event Simulator**: a realistic mid-size org (e.g. 100 users, 50 devices, 20 servers, apps) with a schedule engine that produces hours of daily activity — logins, downloads, file access, network peers — plus switches to *plant anomalies* for each of the 5 canonical cases.
- Build the **Windows Agent** (Python): polls/streams
  - Security log (logon events 4624/4625, privilege use 4672)
  - Sysmon (process/network/file events) if installed
  - File-system watcher (Watcherdog-style) for file events
  - USB device events (PnP)
  - Emits normalized JSON **to Kafka** via producer
- Include a **"raw agent demo" page**: show what the agent captured live (proof of real-time collection).

**Exit criteria:** Simulator and Windows agent both emit schema-valid events to Kafka topics.

### Phase 2 — Kafka Streaming Pipeline
- **Topics:**
  - `auth-events` — logins, logouts, MFA, privilege change
  - `file-events` — access/read/copy/download, size, path, sensitivity
  - `network-events` — connections, source/destination, volume
  - `device-events` — USB, hardware change, agent heartbeats
  - `privilege-events` — permission grants, role changes
- Topic partitioning by `entity_id` so one consumer processes one entity's stream in order.
- Producer client shared by simulator + agents; idempotent producer, at-least-once.
- Consumer group for the analytics engine; deduplication on `event_id` (Postgres unique index) to absorb redeliveries.
- Health/backlog monitoring (consumer lag logged; simple `/health` on engine).

**Exit criteria:** Events flow simulator/agent → Kafka → consumer; consumer prints/ingests and persists them; no duplicates in `raw_events`.

### Phase 3 — Storage Layer
- Full schema via Alembic migrations (tables listed in §4).
- Write path: consumer persists normalized events to `raw_events`.
- Read paths for: engine features, dashboard queries, incident evidence.
- Seed script to generate a starter org (users/entities) so the simulator has identity to drive.

**Exit criteria:** All schema tables exist + seeded org data; event persistence verified end-to-end.

### Phase 4 — Analytics Engine (the core)
- **4.1 Windowed Feature Engineering**
  For each entity, build hour-sized feature vectors:
  - volume (bytes per hour), event count
  - active-hour histogram (when does this entity act)
  - unique peers (dest/source), new-peer rate
  - location set (geolocation/IP clusters, distance between successive logins)
  - department/file-paths touched, file-sensitivity distribution
  - dormant-since (staleness), login failure rate
- **4.2 Baseline Builder**
  - Rolling window (e.g. last 7 days, decayed or re-bucketed daily) per entity.
  - Stores mean/std per feature, allowed sets (locations, peers, dept), active window bounds.
  - Daily rolling retrain so drift is followed.
- **4.3 Isolation Forest**
  - Trained per entity (or per entity-type for sparse data) on normal feature-window vectors.
  - Unsupervised → no labels needed. Predict on each new window: `anomaly_score`.
  - Cheap inference (`O(log n)`) → real-time scoring of streaming windows.
- **4.4 Heuristic Rule Engine** — hard, explainable detectors for the 5 canonical cases:
  1. volume-spike: `current_volume > K × baseline_mean`
  2. impossible-travel: two logins, distance / Δt above speed threshold
  3. out-of-scope: path prefix outside entity's department set
  4. dormant-trigger: entity idle > N days, now active in cold hours
  5. novel-peer: peer id not in entity's peer set
  Every rule emits a **human-explainable reason string** (feeds the dashboard's "why flagged").
- **4.5 Context Enrichment**
  - Role sensitivity tier, department, file sensitivity, admin-privilege flag, hour-of-day risk — multiply/weight found deviations.
- **4.6 Fusion → Risk Score 0–100**
  - `risk = f(ml_score, rule_hits×(weights), context)` — weighted sum with caps; values mapped to bands:
    - 0–24 Low · 25–49 Medium · 50–74 High · 75–100 Critical
- **4.7 Correlation**
  - Cluster related alerts on same `entity_id` + close time window into an incident with a shared timeline; join event evidence.

**Exit criteria:** Given a planted anomaly in the simulator, the engine flags it, emits an explainable reason, and computes a risk score within ~1 feature window of occurrence.

### Phase 5 — Alert / Incident / Response Engine
- **Thresholds:** risk band + entity sensitivity decide `alert` vs `incident`; critical/high auto-escalate to incident.
- **Alert/Incident lifecycle:** `open → assigned → investigating → resolved | false_positive` with timestamps.
- **Response Engine:**
  - Recommended actions per alert type (predefined playbook).
  - Simulated one-click actions: **Force MFA**, **Revoke session**, **Restrict access**, **Isolate device** — recorded as `analyst_actions` (status, who, when, impact summary).
  - Execute harmlessly in the demo (mock the account/device state change + log it).

**Exit criteria:** Anomalies escalate correctly through alert → incident; actions apply and are audited in DB.

### Phase 6 — API Layer
- **FastAPI** REST service, JWT authentication, role-based access:
  - **Analyst:** read dashboard data, review/assign/close alerts and incidents, execute response actions, add notes.
  - **Admin:** analyst account management (invite/disable), risk-threshold tuning, profile/baseline management.
- Endpoints:
  - `POST /auth/login`, `POST /auth/refresh`
  - `GET /overview` (aggregate risk by category), `GET /users`, `GET /entities`
  - `GET /users/{id}/risk`, `GET /entities/{id}/risk` + drill-down timeline
  - `GET /alerts`, `GET /incidents`, `PATCH /alerts/{id}`, `PATCH /incidents/{id}`
  - `POST /incidents/{id}/actions`, `POST /incidents/{id}/notes`
  - `POST /admin/users`, `PUT /admin/thresholds`
- Event/evidence replay endpoint: `GET /incidents/{id}/evidence` (raw events that contributed).
- OpenAPI docs enabled; pydantic response models.

**Exit criteria:** All dashboard screens can fetch real data; roles enforce permissions; evidence replay works.

### Phase 7 — Multi-User SOC Dashboard (React + TypeScript)
- **Auth & roles:** login screen, JWT stored, route guards for `analyst` vs `admin`.
- **Screens:**
  1. **Overview** — org-wide risk summary (totals, top risky users/entities, alert counts by band).
  2. **Users / Entities** — searchable lists, live risk scores, baseline snapshots.
  3. **Entity drill-down** — risk-over-time chart, recent windows, baseline vs actual, rule detections with explanation cards.
  4. **Alerts queue** — filter/assign/open; easy jump-to-incident.
  5. **Incidents** — assignee, status, timeline of correlated events, evidence list, "why flagged" explanations, response-action buttons, notes.
  6. **Admin** (role-gated) — invite analysts, disable accounts, tune thresholds.
- Live updates (WebSocket or short polling from Kafka-side consumer updates) so new alerts appear without refresh.
- Explainability is a first-class UI element: every alert shows *which* rules/features fired and their contribution to the risk score.

**Exit criteria:** Two accounts (analyst + admin) log in; analyst works an incident end-to-end (assign, review evidence, apply action, close); new simul ated anomalies appear live.

### Phase 8 — Integration, Demo Script, Tests, and Runbook
- **End-to-end integration test:** start simulator → events → Kafka → engine → alert → incident → dashboard → action, asserted at each hop.
- **Demo script:** a 10-minute runbook with planted anomalies for all 5 canonical cases and the expected screen states + explanations + risk values.
- **Tests:** unit (analytics features/rules/risk), contract (schema), API integration (pytest), dashboard build/lint.
- **README / runbook:** prerequisites, `docker compose up`, seeding, agent install on a Windows box, how to point admin→analyst, how to demo.

**Exit criteria:** Full pipeline runs from one command; a fresh reviewer can run the demo and see every anomaly detected, explained, scored, and acted upon.

---

## 6. What We Are NOT Building (scope guardrails)

- No blockchain / audit-ledger layer (deferred).
- No real enforcement on live machines — response actions are **simulated** and logged (safe for the demo).
- No SIEM integration, no advanced IAM (AD/Azure AD connectors) in this build.
- No mobile clients; dashboard is desktop-web only.
- Threat hunting / long-arc "insider collusion" analytics out of scope for the checkpoint.

---

## 7. How It Will Be Tied Together (run summary)

1. `docker compose up` brings up **Kafka + PostgreSQL + engine + API**.
2. **Windows agent** (or **simulator**) streams normalized events into Kafka topics continuously.
3. **Engine** (single consumer group) ingests → persists to Postgres → builds per-entity windows → scores with Isolation Forest + rules → fuses context → risk 0–100 → raises alerts/incidents via the API.
4. **Analysts** watch the React dashboard live, investigate with evidence + explanation, and take simulated actions that are audited.
5. **Admins** manage analysts/accounts and thresholds.
6. A planted anomaly travels the entire pipeline in real time — this IS the demo.

---

## 8. Suggested Phase Order & Milestones

- **Checkpoint +1 (first SIH milestone):** Phases 0–4 complete — streaming engine detects all 5 canonical anomalies with explanations + risk scores (can be verified via logs/API before the UI is done).
- **Second milestone:** Phases 5–7 — full alert/incident/response flow + multi-user dashboard live.
- **Final:** Phase 8 — integration tests, demo runbook, polished demo.