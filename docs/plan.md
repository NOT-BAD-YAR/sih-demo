# SIH25244 — User and Entity Behaviour Analytics (UEBA) for Internal Threat Identification — Build Plan (v2, upgraded)

## 1. Overview

We are building a **UEBA (User and Entity Behaviour Analytics)** platform that continuously learns the normal behaviour of every **User, Device, Server, and Application** inside an organization, then detects suspicious deviations in real time.

Behavioral analytics combines **statistical baselines, machine-learning anomaly detection, contextual rules, and event correlation** — it does not claim to "read minds". It detects *anomalous behaviour* and presents context; it does not magically know *malicious intent*. That judgement stays with the SOC analyst.

### The 5 canonical anomalies (official MVP, milestone 1)

1. **Volume spike** — employee normally downloads 20 MB/day, suddenly downloads 5 GB.
2. **Impossible travel** — logins from two geographically distant locations within a short time.
3. **Access outside scope** — an account accesses files outside its department.
4. **Dormant account activation** — an idle account suddenly becomes active at an unusual hour.
5. **Novel peer** — a server contacts a device it has never contacted before.

### Tier-2 anomalies (stretch, after MVP)

6. New device · 7. Privilege escalation · 8. USB data transfer · 9. External cloud upload · 10. Suspicious configuration change · 11. Unusual application usage.

### Excluded for now

- **Blockchain audit layer** — dropped. Evidence trail lives in PostgreSQL. Can be added later as an independent module (Incident → SHA-256 → tamper-evident append) only if the SIH evaluation strongly expects it.
- **Real enforcement on live machines** — response actions are **recommended / simulated** and audited.
- **Redis** — added only if a real performance need emerges. Kafka + PostgreSQL + Python analytics is enough.

---

## 2. Locked Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Streaming | Apache Kafka (real) | Decouples collecting, processing, alerting; real-time by design |
| Behavioral modelling | **Three levels: Individual + Peer-group + Global baselines** | Sparse users have too little history for a reliable per-user model |
| Detection | **Deterministic rules carry the 5 canonical anomalies**; Isolation Forest is a complementary signal | Rules are exact and explainable for deterministic cases |
| Risk scoring | `Risk = Anomaly × Impact × Confidence` mapped to 0–100 | Defensible, non-arbitrary scoring foundation |
| Cold start | New/sparse entities fall back: peer-group → global → build individual | Stops "unknown = suspicious" bias |
| Data sources | **Simulator PRIMARY**, Windows Agent SECONDARY (built after engine works) | Endpoint agents are a project-sized scope risk; simulator proves the UEBA first |
| Dashboard | Multi-user React + TS, Analyst / Admin roles | SOC teams work together; incidents need assignment |
| Storage | PostgreSQL | Relational; Postgres is sufficient for the entity-relationship correlation (no graph DB) |
| Languages | Python (agents, analytics, API) · React + TypeScript (dashboard) | Fits the stack and demo velocity |

---

## 3. Architecture (full view, upgraded)

```
                     ENTERPRISE
                         │
         ┌───────────────┼─────────────────┐
         ↓               ↓                 ↓
      Users           Devices           Servers        Apps · IAM · Network
         └───────────────┼─────────────────┘
                         ↓
                EVENT COLLECTION
              Simulator (primary)                    ┌─ Win agent (Phase 8)
              Multi-stage scenarios                  │  Security log · Sysmon ·
              Backfilled history                     │  file · USB · process
              + Agent later                          └  normalized JSON
                         ↓
                 EVENT NORMALIZATION (common schema)
                         ↓
                      KAFKA
      auth-events · file-events · network-events · device-events · privilege-events
                         │
                 ┌───────┴────────┐
                 ↓                ↓
             PostgreSQL      ┌──────────────────────────────┐
                             │       ANALYTICS ENGINE       │
                             │  Event Processor → validate  │
                             │  Feature Engine (windows)    │
                             │  Baseline Engine             │
                             │   ┌─────────┼──────────┐     │
                             │  Index     PeerGroup  Global│
                             │  Baseline  Baseline   Base  │
                             │   └─────────┼──────────┘     │
                             │  Confidence per level        │
                             │  ┌─────────┴──────────┐      │
                             │ Rule Engine      ML Engine   │
                             │ (deterministic)  Isolation   │
                             │                  Forest      │
                             │  └─────────┬──────────┘      │
                             │      Context Engine          │
                             │      Risk Engine             │
                             │      Correlation Engine      │
                             └──────────────┬───────────────┘
                                            ↓
                              Alert / Incident (0–100 risk)
                                            ↓
                      ┌─────────────────────┴──────────────────┐
                      ↓                                         ↓
             SOC DASHBOARD (React)                    RESPONSE ENGINE
             multi-user, roles                     recommended + simulated
             Overview · Users/Entities             actions, audited
             Entity Investigation ★                (MFA · revoke · restrict · isolate)
             Alerts · Incidents · Admin
                      ↓
              Analyst investigation + notes
                      ↓
                  PostgreSQL audit
```

**End-to-end flow of one anomaly (the Account Compromise chain):**

```
02:13 EMP104 logs in from Delhi      → auth-events → Kafka
02:14 unknown device added           → device-events → Kafka
02:16 Finance DB access              → file-events → Kafka
02:21 4.8 GB download                → file-events → Kafka
02:24 external upload                → network-events → Kafka
    → Feature Engine builds windows
    → Baseline Engine: individual (new-location spike) + peer-group deviation + global check
    → Rule Engine: impossible travel hit · out-of-scope hit · volume-spike hit
    → ML (Isolation Forest): windowed anomaly signal
    → Context: admin role, sensitive Finance resource, 2 AM
    → Risk = Anomaly(0.9) × Impact(0.8) × Confidence(0.9) → ~65/100 → Incident (High)
    → Correlation: 5 events + 4 entities form ONE incident timeline
    → Dashboard: analyst sees "why flagged" cards + evidence + timeline
    → Analyst assigns, reviews, applies "Revoke session + Force MFA" (simulated)
    → All actions + notes audited in PostgreSQL
```

---

## 4. Data Model (PostgreSQL — core tables)

- `users` — employees (name, role, department, peer_group_id, sensitivity_tier, dormant flag)
- `peer_groups` — HR, Finance, Developers, DevOps, Administrators, Security, Contractors
- `entities` — devices, servers, apps (type, hostname, location, owner_id)
- `raw_events` — every normalized event; **dedupe key = `event_id UNIQUE`**
- `behavioral_profiles` — per entity, **per level** (individual / peer_group / global):
  feature stats, sets, active windows, **sample counts + confidence (HIGH/MED/LOW)**, as-of date
- `feature_windows` — hour-sized feature vectors used for training/scoring
- `alerts` — individual detections (severity, risk, evidence refs, status)
- `incidents` — correlated cluster (entity chain `actor→source→target→peer`, assigned_to, status, notes)
- `users_accounts` — dashboard logins (hashed password, role: analyst/admin)
- `analyst_actions` — audit trail (who did what, when, impact summary)
- `ground_truth` — simulator's planted-anomaly labels (for evaluation metrics only)

**Entity-relationship fields on every event** (enable graph-style correlation in Postgres):
`event.actor · event.source_entity · event.target_entity · event.peer_entity`

---

## 5. Behavioural Baseline Design (the core idea)

**Three levels, used together:**

1. **Individual baseline** — EMP104: login ~09:00, Chennai, Laptop-104, HR files, 40 MB/day.
2. **Peer-group baseline** — HR group: 09:00–18:00, HR resources, Chennai, 20–100 MB/day.
3. **Global baseline** — org-wide login/network/transfer patterns.

**Baseline windows:**

- Warm-up: 14–30 days (for a new user, before individual judgement)
- Primary baseline: 30 days
- Recent-behaviour windows: last 1h / 6h / 24h (scored fast)
- Long-term statistics: 90 days (simulator backfills instantly; real data accrues)

**Cold-start fallback:** new/sparse entity → start with peer-group baseline → cross-check global → gradually build individual baseline as sample count grows.

**Baseline confidence:** every stat stores `mean · std · samples · confidence`. A user with `samples=12, confidence=LOW` is judged gently; `samples=438, confidence=HIGH` is judged strictly.

**Rolling retrain:** baselines re-bucket daily on a rolling window so drift is followed; no per-event full retrain.

---

## 6. Detection: Rules + ML + Context → Risk

### 6.1 Rule Engine (deterministic — owns the 5 canonical cases)

Every rule emits an **explainable reason string** (feeds "why flagged"):

1. **Volume spike** — `current_volume > K × baseline_mean` (individual & peer-group baseline)
2. **Impossible travel** — two logins; `distance / Δt > speed threshold` (explicit geodistance calc, no ML)
3. **Out-of-scope access** — path/dept prefix ∉ entity's allowed set
4. **Dormant trigger** — `last_activity > N days`, now active in cold hours
5. **Novel peer** — peer id ∉ known_peer_set, weighted by peer_frequency + recent_peer_history

### 6.2 ML Engine (Isolation Forest — complementary, not the lead)

- Trained at three levels too: individual (rich data), peer-group, global (sparse entities fall back).
- Unsupervised — no labels needed.
- Cheap inference (`O(log n)`) → real-time scoring of streaming windows.
- Produces a 0–1 `anomaly` signal that *supplements* rule hits; never used alone as "malicious".

### 6.3 Context Engine

Builds a behavioural context vector from each event:

```
WHO        EMP104      USING WHAT  Unknown device     WHEN      2:13 AM
DOING WHAT Access Fin. FROM WHERE  Delhi              HOW MUCH  4.8 GB
TARGET     Sensitive Finance resource (sensitivity tier)
attribute  → role sensitivity · department · admin-privilege · hour-of-day risk
```

### 6.4 Risk Engine — `Risk = Anomaly × Impact × Confidence`

Three distinct 0–1 concepts (never additive-capped):

- **Anomaly** — how unusual is this behaviour? (from rules + IF deviation)
- **Impact** — how dangerous is the resource/action? (target sensitivity, role)
- **Confidence** — how sure are we the signal is meaningful? (baseline confidence, evidence strength)

```
Risk(0–100) = Anomaly × Impact × Confidence × 100   (+ small rule-bonus, capped)
Bands: 0–24 Low · 25–49 Medium · 50–74 High · 75–100 Critical
```

The band + entity sensitivity decides `alert` (low) vs `incident` (high escalates).

### 6.5 Correlation Engine

- Cluster related events/alerts into incidents via the **entity chain** (`actor → source_entity → target_entity → peer_entity`) + close time window.
- "These 4 entities and 8 events form ONE suspicious chain" → one incident timeline; each contributing event kept as evidence.
- Postgres is sufficient — no graph database.

---

## 7. Kafka Delivery Semantics (corrected)

```
Kafka delivery:   At-least-once
Application:      Idempotent processing (dedupe by event_id)
Database:         event_id UNIQUE constraint
```

If Kafka redelivers `EVT123`, PostgreSQL rejects the duplicate insert — no double-processing. This claim is simple and defensible; we do **not** claim exactly-once.

Topics (partitioned by `entity_id` for per-entity ordering): `auth-events`, `file-events`, `network-events`, `device-events`, `privilege-events`.

---

## 8. Simulator (primary data source — build it properly)

- A realistic mid-size org: **100 employees · 50 devices · 20 servers · 10 apps · 5 departments** + peer groups.
- Schedule engine produces **90 days of normal behaviour** as backfilled history, then continues live.
- **Planted anomalies (labeled → ground_truth):**
  - 5 single canonical scenarios (volume spike, impossible travel, out-of-scope, dormant, novel peer)
  - **Multi-stage scenarios** — e.g. Account Compromise chain: new location → new device → sensitive access → large download → external upload. Proves correlation, not just detection.
- Every planted anomaly is recorded in `ground_truth` so **Precision / Recall / F1 / FPR / latency** are measurable.
- Deterministic seed → reproducible demo.

## 9. Windows Agent (secondary — Phase 8, must not block the engine)

- Reads Security log (4624/4625/4672), Sysmon (if installed), file-system watcher, USB/PnP events, process events.
- Emits normalized JSON → Kafka.
- Demo value: "these events aren't only synthetic; our endpoint collector produces real events."
- **Priority: simulator first → UEBA works → dashboard works → then real endpoint data.**

---

## 10. Phases (re-ordered — engine first, agent last, evaluation built in)

### Phase 0 — Infrastructure Scaffolding
- Monorepo: `agents/ · simulator/ · streaming/ · analytics/ · api/ · dashboard/ · db/ · tests/ · docs/`
- `docker-compose`: Kafka (KRaft), PostgreSQL, engine, API; env config; health checks; Alembic migrations skeleton.

**Exit:** `docker compose up` starts Kafka + Postgres cleanly; empty tables created.

### Phase 1 — Event Schema + Simulator
- **Common Event Schema** (normalized fields, incl. `actor/source/target/peer` + `ground_truth` label hooks).
- **Simulator**: 100 users / 50 devices / 20 servers / 10 apps / 5 depts + peer groups; 90-day backfill; live scheduler; plant 5 canonical anomalies.

**Exit:** Simulator emits schema-valid events; ground_truth populated.

### Phase 2 — Kafka Streaming
- Topics, partition-by-entity, producer (idempotent), consumer group, `event_id` dedupe, topic health/lag logging.

**Exit:** Events flow simulator → Kafka → consumer, persisted with no duplicates.

### Phase 3 — PostgreSQL Storage
- Full schema via migrations; seed org (users, entities, peer_groups); write path from consumer.

**Exit:** All tables exist; seeded org drives the simulator; event persistence verified.

### Phase 4A — Behavioral Baselines
- Windowed feature engineering (volume, active-hours, peers, locations, dept-paths, sensitivity, staleness, login-failure).
- 3-level baseline builder (individual / peer-group / global) + confidence stats + cold-start fallback + daily rolling retrain.

**Exit:** Baselines build for seeded org; confidence and cold-start visible in data.

### Phase 4B — Rule Detectors
- Deterministic engines for the 5 canonical cases, each emitting an explainable reason string.

**Exit:** Each of the 5 planted anomalies is detected and *explained* in plain language.

### Phase 4C — ML Anomaly Detection
- Isolation Forest at 3 levels; windowed scoring; fallback to peer/global for sparse entities.

**Exit:** IF emits supplementary anomaly signals; output combined with rules.

### Phase 4D — Context + Risk
- Context vector per event + `Risk = Anomaly × Impact × Confidence` → 0–100 + bands.

**Exit:** Risk scores are reproducible, bounded, and defensible per the formula.

### Phase 4E — Correlation
- Entity-chain clustering → multi-event incident timelines with evidence.

**Exit:** Multi-stage (Account Compromise) scenario folds into ONE incident.

### Phase 5 — Alert / Incident / Response Engine
- Thresholds & escalation, lifecycle (open→assigned→investigating→resolved | false_positive).
- **Response engine**: recommended + simulated actions (Force MFA, Revoke session, Restrict access, Isolate device) — audited in `analyst_actions`.

**Exit:** Anomalies escalate; actions apply and are audited.

### Phase 6 — API Layer (FastAPI)
- JWT auth + roles (Analyst: view/review/assign/close/act; Admin: manage analysts, tune thresholds).
- Endpoints: auth, overview, users/entities, risk drill-down, alerts, incidents, actions, notes, evidence replay (`GET /incidents/{id}/evidence`), admin.

**Exit:** All dashboard screens fetch real data; roles enforced; evidence replay works.

### Phase 7 — Multi-User SOC Dashboard (React + TS)
- Auth + role-guarded routes. Screens: **Overview · Users/Entities · Entity Investigation ★ · Alerts · Incidents · Admin**.
- **Entity Investigation is the flagship page** — normal → current → deviation → reason → risk → timeline → incident.
- "Why flagged" cards (rule + feature contribution). Live updates via polling/WS.

**Exit:** Analyst logs in and works an incident end-to-end; new anomalies appear live.

### Phase 8 — Windows Agent
- Endpoint collector (Security logs, Sysmon, file, USB, process) → normalized JSON → Kafka. Prototype-lite: don't let this block anything else.

**Exit:** A real Windows box streams real endpoint events the engine understands.

### Phase 9 — Evaluation + Demo
- **Metrics** (simulator = ground truth): Detection rate/Recall, Precision, False Positive Rate, Detection latency (event→alert seconds), Incident correlation accuracy.
- **Ablation study** (great technical slide):
  `Baseline-only  vs  Baseline + Rules  vs  Baseline + Rules + ML` across 30 days normal → Day-31 injection.
- **Demo runbook** (10 min): all 5 canonical anomalies + 1 multi-stage chain, expected screen states, explanation cards, risk values, actions.
- Tests (unit: features/rules/risk; contract: schema; API integration; dashboard lint/build).
- README runbook: prerequisites, `docker compose up`, seeding, agent install, demo walkthrough.

**Exit:** Full pipeline runs from one command; a fresh reviewer reproduces the demo and sees every anomaly detected, explained, scored, correlated, and acted upon.

### Phase 10 — Optional stretch
- Tier-2 anomalies (new device, privilege escalation, USB, cloud upload, config change, unusual app usage).
- Blockchain audit module (independent): Incident → SHA-256 → Hyperledger Fabric → audit verification.

---

## 11. Milestones

- **Checkpoint +1 (first SIH milestone):** Phases 0–4E + Phase 9 evaluation snapshot — streaming engine detects all 5 canonical anomalies + 1 multi-stage chain, with explanations, defensible risk scores, and measured Recall/Precision (verifiable via API/logs before UI).
- **Second milestone:** Phases 5–7 — alert/incident/response flow + multi-user dashboard.
- **Final:** Phase 8 (Windows Agent) + Phase 9 demo runbook + Tier-2/blockchain stretch as time permits.

---

## 12. What We Are NOT Building (scoreboard)

- No blockchain in the first build.
- No real enforcement — simulated response actions only.
- No Redis unless a measured need appears.
- No SIEM / AD / Azure AD integrations.
- No mobile clients.
- No threat-hunting / long-arc collusion analytics for this checkpoint.

---

## 13. Principles (language discipline)

- Describe the system as **"behavioral analytics combining statistical baselines, ML anomaly detection, contextual rules, and event correlation"** — not "AI-powered threat detection".
- The ML model detects **anomalous behaviour**, never "malicious intent".
- Response actions are **recommended / simulated**.
- Risk scoring is **Anomaly × Impact × Confidence**, never "add scores and cap".