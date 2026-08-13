# SIH25244 — UEBA Low-Level Design (LLD)

**Project:** User and Entity Behaviour Analytics for Internal Threat Identification
**Document:** Low-Level Design covering Phases 0–9 (Phase 10 stretch)
**Companion docs:** `plan.md` (high-level build plan) · `architecture.md` (overview diagram) · `ps.txt` (problem statement)

---

## Document Conventions

- **Modules** are described as Python packages (analytics side) and TS feature folders (dashboard side).
- Every section lists: **Goal → Modules → Key functions/classes (signatures) → Data structures → Logic → Flows → Exit criteria → Connect to next phase**.
- Function signatures use Python type-annotation style in `monospace`. Dashboard components show props / hooks flow.
- Schema is described as column tables (not full SQL DDL) — exact DDL is generated at implementation via Alembic.
- Config lives in `.env`; every module reads config through a single `config.py` (analytics/api) or typed `env` module (dashboard).
- All identifiers are `snake_case` (Python) / `camelCase` (TS) / `lower_snake` (DB).

---

# PHASE 0 — INFRASTRUCTURE SCAFFOLDING

## Goal
Stand up the monorepo skeleton and infrastructure containers so every later phase has a stable home to run inside.

## Modules & Files

```
insider-threat/
  docker-compose.yml
  .env.example
  Makefile                        # dev shortcuts (up, down, logs, migrate, seed)
  README.md
  agents/            (Phase 8)
  simulator/         (Phase 1)
  streaming/         (Phase 2)
  analytics/         (Phases 4A–4E)
  api/               (Phase 6)
  dashboard/         (Phase 7)
  db/                (Phase 3)
  tests/             (across)
  docs/
```

## Key Components (signatures + logic)

### `docker-compose.yml`
Services: `kafka` (KRaft — no ZooKeeper), `postgres`, `engine` (analytics consumer), `api` (FastAPI), `dashboard` (dev Vite). Volumes for Kafka and Postgres data. Health-checks gate each service (Postgres `pg_isready`, Kafka via client probe).

### `analytics/config.py`
```python
from dataclasses import dataclass, field
from typing import Optional
import os

@dataclass(frozen=True)
class Config:
    kafka_bootstrap: str
    kafka_group_id: str
    postgres_dsn: str
    warmup_days: int = 14
    primary_window_days: int = 30
    long_term_days: int = 90
    rule_volume_threshold_k: float = 5.0
    impossible_travel_speed_kmh: float = 600.0
    dormant_days: int = 30
    risk_band_high: int = 50
    risk_band_critical: int = 75

    @classmethod
    def from_env(cls) -> "Config": ...
```
Logic: read `.env` once per process; every module imports the singleton. Values feed all downstream thresholds and windows so tuning is centralized.

### `db/migrations` skeleton (Alembic)
`alembic.ini`, `env.py` wired to Postgres DSN, `versions/` directory empty at Phase 0. Migration practice: one migration file per schema change, forward-only.

## Flows
1. `docker compose up` → Postgres accepts connections, Kafka brokers online.
2. `make migrate` → Alembic runs no-op → tables created later in Phase 3.
3. All services log to stdout; `make logs` tails everything.

## Exit criteria
`docker compose up` is clean; Postgres and Kafka are healthy; Alembic connects to the DB; the repo layout exists.

## Connect to next phase
Phase 1 shoes the simulator into `simulator/` and streams to the Kafka bootstrap servers that run here.

---

# PHASE 1 — EVENT SCHEMA + SIMULATOR (PRIMARY DATA SOURCE)

## Goal
Define the single normalized event schema and build a deterministic simulator that produces 90 days of realistic organization activity (backfilled), lives on for real-time generation, and plants labeled anomalies for all 5 canonical cases + multi-stage chains.

## 1.1 Common Event Schema

Every source (simulator, agent) emits this same shape. Fields:

| Field | Type | Validation / Notes |
|---|---|---|
| `event_id` | string (UUID) | Unique per event; dedupe key in DB |
| `ts` | datetime (UTC) | Event time |
| `ingested_at` | datetime (UTC) | Set by producer/consumer |
| `entity_type` | enum `user|device|server|app` | The actor kind |
| `entity_id` | string | Actor identifier (EMP104, LPT-104, SRV-21) |
| `user_id` | string | Resolved user (from device→owner link if actor is device) |
| `event_type` | enum `login|logout|file_access|download|upload|network_conn|usb|process|privilege|mfa|failure` | Category |
| `actor` | string | Subject doing the action (legacy flat + graph key) |
| `source_entity` | string | Device/app the action originated from |
| `target_entity` | string | Resource acted on (file, DB, server) |
| `peer_entity` | string | Network peer / destination |
| `ip` | string | Source IP; geocoded offline |
| `geo` | object `{city, lat, lon}` | Resolved from IP in simulation data |
| `file_path` | nullable string | For file events |
| `bytes` | int | Volume (download/upload) |
| `outcome` | enum `success|failure` | |
| `sensitivity` | enum `public|internal|confidential|restricted` | Resource tier |
| `raw_payload` | JSONB | Source-specific details preserved |

`ground_truth` label (seed only, **not** a shipping event field): the simulator records planted scenarios in `ground_truth` table so evaluation can compute Precision/Recall.

## 1.2 Simulator Architecture

Package `simulator/`:

```
simulator/
  __init__.py
  org.py            # organization + schedule generators
  backfill.py       # 90-day historical generation
  live.py           # real-time scheduler service
  anomaly.py        # anomaly injection engine
  ground_truth.py   # ground-truth record writer
  engine.py         # main orchestration entrypoints
```

### Key functions (signatures + logic)

**`simulator/org.py`**
```python
@dataclass
class Employee:
    emp_id: str; name: str; department: str
    peer_group: str; role: str; sensitivity_tier: str
    geo: str; device_id: str; active_hours: tuple[int, int]
    calm: float                       # base inter-event delay variance

def generate_org(seed: int = 42) -> list[Employee]
# Generates 100 employees across 5 departments (HR, Finance, Developers, DevOps, Security)
# + 20 contractors (stretch). Assigns device(s), office location, working hours.
# Peer groups = departments + "Administrators" + "Contractors".

def generate_devices(org) -> list[Device]         # 50 devices, owner link, capabilities
def generate_servers(org) -> list[Server]         # 20 servers, allowed peers per dept
def generate_apps(org) -> list[App]               # 10 apps (Finance DB, HRMS, git, mail ...)

def assign_sensitivity_resource_map(org) -> dict[str, Resource]
# Maps file/DB resources to sensitivity tiers + owner departments (HR files confined to HR dept,
# Finance DB to Finance dept) — powers "out-of-scope" detection.
```
Logic: deterministic with `random.Random(seed)` so every demo run is identical. Department→resource access matrix is the source of truth for out-of-scope rule truth.

**`simulator/engine.py`**
```python
def generate_event(emp, rng, now) -> RawEvent | None
# Chooses an event_type per employee's activity profile/probabilities (e.g. HR logs in ~09:00,
# opens HRMS, downloads 20–60 MB reports). Applies active_hours. Emits validated event per Common Schema.
def run_backfill(org, days: int = 90, out_fn) -> None
# Walks day-by-day for each employee, calls generate_event for each active session,
# writes to Kafka (or fast-parquet/batch loader for speed during backfill).
def run_live(org, interval_sec: float = 5.0) -> None
# Long-running loop: each tick schedules the next batch of events per employee,
# sends through the same producer path as the Windows agent (uniform pipeline).
```

**`simulator/anomaly.py`**
```python
def inject_scenario(scenario: str, org, rng, now) -> list[RawEvent]
```
Five single scenarios plus configurable multi-stage chains. Each plant writes a `ground_truth` row.

| Scenario | Logic |
|---|---|
| `volume_spike` | Target employee's normal scale × 8–20× for one session (e.g. 5 GB in 1 hour) |
| `impossible_travel` | Two logins from CN→DL within Δt such that speed > 600 km/h |
| `out_of_scope` | Employee touches `target` from a department outside their matrix (e.g. HR → Finance DB) |
| `dormant` | A user with `last_activity` ≥ 30 days ago activates at ~02:30 |
| `novel_peer` | A server connects to an IP/device absent from its 90-day peer set |
| `compromise_chain` | Curated sequence: new location → new device → sensitive access → large download → external upload over ~12 minutes (proves correlation) |

**`simulator/ground_truth.py`**
```python
def record(scenario: str, entity_id: str, start: datetime, end: datetime,
           related_event_ids: list[str], rule: str, expected_risk_band: str) -> None
```
Writes to `ground_truth` table. Used only by evaluation (Phase 9), never by the live risk path.

## Flows
1. `backfill()` builds 90 days history → baselines come pre-warmed.
2. `live()` streams events → Kafka, identical to how the agent will stream.
3. Anomalies are scheduled by scenario scripts with known timestamps → later replayed for the demo.

## Exit criteria
Simulator emits schema-valid events; backfill produces ~90 days of data quickly (bulk path); all 5 anomaly types plus one chain are generated with ground-truth rows.

## Connect to next phase
Events go through the Kafka producer in Phase 2; the consumer (Phases 4+) reads them.

---

# PHASE 2 — KAFKA STREAMING PIPELINE

## Goal
Reliable real-time transport: topics, partitioning, production (simulator + agent), consumption (engine), with idempotent dedupe.

## Modules & Files

```
streaming/
  topics.py          # topic names + partition config
  producer.py        # AsyncProducer wrapper
  consumer.py        # ConsumerGroup wrapper + worker
  monitor.py         # backlog/lag logging + /health
```

### Topics (constant table in `topics.py`)

| Topic | Event types | Partitions | Key |
|---|---|---|---|
| `auth-events` | login, logout, mfa, failure | 4 | `user_id` |
| `file-events` | file_access, download, upload | 4 | `entity_id` |
| `network-events` | network_conn | 4 | `source_entity` |
| `device-events` | usb, process, privilege | 4 | `entity_id` |
| `privilege-events` | privilege | 2 | `user_id` |

Partitioning by `entity_id` guarantees per-entity ordering for the engine.

### Key classes (signatures + logic)

**`streaming/producer.py`**
```python
class EventProducer:
    def __init__(self, bootstrap: str, topic: str): ...
    async def send(self, event: dict) -> None
    async def close(self) -> None
    @staticmethod
    def _kafka_key(event: dict) -> bytes      # entity_id/user_id per topics.py
```
Logic: `acks=all`, `enable.idempotence=True` (Kafka-side dedupe of producer retries). Exposed to simulator and agent as the single "ship an event" path.

**`streaming/consumer.py`**
```python
class EngineConsumer:
    def __init__(self, bootstrap: str, group_id: str, topics: list[str],
                 handler: Callable[[dict], None]): ...
    def run(self) -> None
    def _handle(self, msg: KafkaMessage) -> None
```
Logic: `auto.offset.reset=earliest`, `enable.auto.commit=False`; the consumer commits an offset only after the handler successfully persisted the event (at-least-once delivery). Handler is the analytics ingestion callback (Phase 4 start).

**Dedupe (on database)**
- Consumer handler inserts with `ON CONFLICT (event_id) DO NOTHING`.
- If insert skipped, event is already known → no double processing (idempotent application).

**`streaming/monitor.py`**
```python
def log_lag(consumer, interval_sec: int = 30): ...
def health() -> dict            # {"kafka": bool, "topics": {...}, "lag": {...}}
```
Logic: reports partitions, current offsets, end offsets, lag. `/health` endpoint on the engine surfaces this for dashboard/ops.

## Flows
1. Producer (simulator/agent) → `send(event)`.
2. Engine consumer reads topic, calls handler → handler normalizes/persists (Phase 4) → commit.
3. Redelivered event hits unique `event_id` → rejected → no duplicate.

## Exit criteria
Events flow simulator→Kafka→consumer with zero duplicates in `raw_events`; lag is visible; `/health` reports healthy.

## Connect to next phase
Consumer persistence writes into the Phase 3 PostgreSQL schema.

---

# PHASE 3 — POSTGRESQL STORAGE

## Goal
Persistent storage for events, identity, baselines, alerts, incidents, audits — with indexes that serve both engine writes and dashboard reads.

## Schema (tables, columns, constraints)

### `users`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `emp_id` | varchar UNIQUE | EMP104 |
| `name`, `department`, `peer_group_id`, `role`, `sensitivity_tier` | varchar | peer_group FK |
| `primary_device_id` | FK | |
| `office_geo` | varchar | for impossible-travel distance |
| `last_activity_at` | timestamptz | dormant logic |
| `created_at` | timestamptz | |

### `peer_groups`
| Column | Type |
|---|---|
| `id` | SERIAL PK |
| `name` | varchar UNIQUE (HR, Finance, Developers, DevOps, Administrators, Security, Contractors) |
| `baseline_features` | JSONB (aggregate stats per feature — mean/std/sets/window) |

### `entities`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `entity_id` | varchar UNIQUE | LPT-104 / SRV-21 |
| `kind` | enum device|server|app | |
| `owner_user_id` | FK nullable | devices owned by employees |
| `location`, `ip` | varchar | |

### `raw_events`
| Column | Type | Notes |
|---|---|---|
| `event_id` | uuid PK | **UNIQUE dedupe key** |
| `ts`, `ingested_at` | timestamptz | ingest index: `(ts)` |
| `entity_type`, `entity_id`, `user_id` | varchar | index `(user_id, ts)` |
| `event_type` | enum | |
| `actor`, `source_entity`, `target_entity`, `peer_entity` | varchar | index `(actor)`, `(peer_entity, ts)` — graph correlation |
| `ip`, `geo` (JSONB) | | |
| `file_path`, `bytes`, `outcome`, `sensitivity` | | |
| `raw_payload` | JSONB | |

### `behavioral_profiles`
| Column | Type | Notes |
|---|---|---|
| `id` | SERIAL PK | |
| `entity_ref` | varchar | entity_id OR peer_group OR `__global__` |
| `level` | enum individual|peer_group|global | |
| `feature_stats` | JSONB | `{feature: {mean, std, count, confidence}}` |
| `allowed_sets` | JSONB | `{locations:[...], peers:[...], dept_paths:[...], sensitivity:[...]}` |
| `active_window` | JSONB | `{start_hour, end_hour}` |
| `confidence` | enum HIGH|MED|LOW | computed from sample count |
| `updated_to` | timestamptz | rolling bucket as-of |

### `feature_windows`
| Column | Type |
|---|---|
| `id` | SERIAL PK |
| `entity_ref` | varchar |
| `window_start` | timestamptz (hour granularity) |
| `vector` | JSONB `{volume, event_count, active_hours_frac, unique_peers, new_peer_count, location_count, dept_distinct, sensitivity_hist, fail_rate, staleness_days}` |
| `ts` | timestamptz |

### `alerts` / `incidents`
Both carry: `id`, `entity_ref`, `severity/band`, `risk` (0–100), `status` (open/assigned/investigating/resolved/false_positive), `evidence_refs` (jsonb list of event_ids), `created_at`, `assigned_to`.
`incidents` adds: `entity_chain` (JSONB `[actor, source, target, peer]`), `related_alert_ids`, `notes` (jsonb).

### `users_accounts`
`id`, `username` UNIQUE, `password_hash` (argon2/bcrypt), `role` (analyst/admin), `disabled`.

### `analyst_actions`
`id`, `incident_id` FK, `action` (enum mfa/revoke/restrict/isolate), `actor_user`, `impact` (JSONB summary), `created_at`.

### `ground_truth`
`id`, `scenario`, `entity_id`, `start/end`, `related_event_ids` (jsonb), `rule`, `expected_risk_band`. **Used only by Phase 9 evaluation.**

## Key components
- `db/migrations/` — Alembic versions; one migration per change.
- `db/seed.py` — loads org (users, peer_groups, entities, resources), creates analyst+admin demo accounts with hashed passwords.
- `db/dao.py` — typed repository functions (`insert_event`, `get_profile`, `upsert_window`, `create_incident`...) used by engine and API (shared package `db/` imported by both).

## Flows
1. Engine consumer → `dao.insert_event` (ON CONFLICT DO NOTHING).
2. Dashboard reads via API DAOs, never direct SQL.

## Exit criteria
Migrations apply cleanly; seeded org present; insert/read paths verified; dedupe works end-to-end.

## Connect to next phase
Phase 4 reads/writes profiles + windows and streams risk results back into alerts/incidents.

---

# PHASE 4 — ANALYTICS ENGINE (the core)

Internal pipeline (from upgrade.md): `Event Processor → Feature Engine → Baseline Engine → (Rule Engine ∥ ML Engine) → Context Engine → Risk Engine → Correlation Engine → INCIDENT`.

Package `analytics/`:

```
analytics/
  processor.py      # 4.1 normalize/validate ingress
  features.py       # 4.1 windowed feature construction
  baseline.py       # 4.2 three-level baseline builder + confidence + cold start
  rules/            # 4.3 rule detectors (one module per rule)
    __init__.py     # RuleResult type + registry
    volume_spike.py
    impossible_travel.py
    out_of_scope.py
    dormant.py
    novel_peer.py
  ml.py             # 4.4 Isolation Forest engine
  context.py        # 4.5 context vector
  risk.py           # 4.6 Risk = Anomaly × Impact × Confidence
  correlation.py    # 4.7 entity-chain clustering
  runner.py         # orchestrates the pipeline over each consumed window
```

## 4.1 Event Processor + Feature Engine

**`processor.py`**
```python
@dataclass(frozen=True)
class NormalizedEvent:
    # mirrors Common Event Schema; enums validated on ingestion

def validate(raw: dict) -> NormalizedEvent | None
# Schema check: required fields present, enums valid, ts sane. Invalid → dropped + logged (never crashes).
def resolve_user(ref: NormalizedEvent) -> str
# If actor is a device, map device → owner user via entities table (cold-start aware).
```

**`features.py`**
```python
@dataclass
class FeatureWindow:
    entity_ref: str
    window_start: datetime        # hour boundaries
    volume: int
    event_count: int
    active_hours_frac: float      # share of hours active
    unique_peers: set[str]
    new_peer_count: int
    location_count: int
    location_dist_km: float       # pairwise session distances in window
    dept_distinct: set[str]       # departments touched
    sensitivity_hist: dict[str, int]
    fail_rate: float
    staleness_days: int           # days since last activity before window

def accumulate(existing: FeatureWindow | None, ev: NormalizedEvent) -> FeatureWindow
def finalize(w: FeatureWindow) -> dict      # JSONB-serializable vector
```
Logic: window = hour bucket keyed on `(entity_ref, hour)`. Accumulation is incremental (maintained in memory + upserted to `feature_windows`); `finalize` at bucket close feeds scoring.

## 4.2 Baseline Engine (three levels + confidence + cold start)

**`baseline.py`**
```python
@dataclass
class BaselineStats:
    mean: float; std: float; count: int; confidence: str

def confidence_for(count: int) -> str     # <20 LOW · 20–100 MED · >100 HIGH

class BaselineBuilder:
    def build_individual(entity_ref: str, windows: list[dict]) -> behavioral_profiles.row
    def build_peer_group(peer_group_id: str, member_profiles: list) -> row
    def build_global(all_profiles: list) -> row

def rolling_retrain(entity_ref: str) -> None
# Daily job: rebuild individual profile from last 30 days of windows;
# peer-group from members' 30-day stats; global from all. Keeps drift-following.

def select_level(entity_ref: str, user: Employee) -> ProfileLevel
# Cold start: if individual.count < 20 → use peer_group; if peer-group sparse → global.
# Returns (level, profile_row) the risk engine should score against.
```
Logic takeaways (hard truths implemented):
- **Never train a per-entity model with poor history** — sparse users automatically judge against peer-group/global until `count ≥ 20`.
- Every stat stores `count + confidence` so the risk engine weights LOW-confidence comparisons gently.
- New user: peer-group baseline first, individual grows in; this is the **cold-start mechanism**.

## 4.3 Rule Engine (deterministic — owns the 5 canonical cases)

**`rules/__init__.py`**
```python
@dataclass
class RuleResult:
    rule: str
    triggered: bool
    severity: float           # 0–1 anomaly contribution
    explanation: str          # human-readable "why flagged" sentence
    evidence: list[str]       # event_ids

REGISTRY: list[type["Rule"]] = [...]   # all rules; lookup by name
```

**`volume_spike.py`**
```python
def evaluate(window, profile_individual, profile_peer_group) -> RuleResult
```
Logic: `ratio = window.volume / max(profile.mean, 1)`; trigger if `ratio > K` (default K=5), OR peer-group ratio > 1.5× group mean with LOW influence weight. Explanation e.g. `"Volume 250× individual baseline (40MB→10GB)"`.

**`impossible_travel.py`**
```python
def evaluate(login_pairs: list[tuple[geo, ts]]) -> RuleResult
```
Logic: for consecutive logins compute haversine distance `d`; `speed = d / Δt`; trigger if `speed > 600 km/h`. **Deterministic — no ML involved.**

**`out_of_scope.py`**
```python
def evaluate(ev, access_matrix: dict[dept, set[resource]]) -> RuleResult
```
Logic: `file_path`/`target_entity` resolved to a resource; trigger if resource's owning dept ∉ user's department allowed set (from org matrix + `allowed_sets`). Explanation cites which dept it belongs to.

**`dormant.py`**
```python
def evaluate(profile: BaselineStats, ev) -> RuleResult
```
Logic: trigger if `staleness_days > N_dormant` (30) AND event hour ∉ user's `active_window`. Explanation includes dormant duration + cold hour.

**`novel_peer.py`**
```python
def evaluate(ev, peer_profile: BaselineStats) -> RuleResult
```
Logic: trigger if `peer_entity ∉ known_peer_set` and `peer_frequency(peer) == 0` (first-ever). Uses `known_peer_set + peer_frequency + recent_peer_history`, not ML.

## 4.4 ML Engine (Isolation Forest — complementary)

**`ml.py`**
```python
MODEL_CACHE: dict[str, IsolationForest]     # keyed by level+entity_ref

def featurize(windows: list[FeatureWindow]) -> np.ndarray   # [+features as float matrix]

def train(level: str, entity_ref: str, history_windows: list[FeatureWindow]) -> None
# sklearn.ensemble.IsolationForest(n_estimators=100, contamination=0.01, random_state=42)
# Trained on normal windows (all samples treated as normal prior).

def score(level: str, entity_ref: str, window: FeatureWindow) -> float   # returns 0–1 anomaly
# decision_function → z → sigmoid → 0–1. Cache miss/not-trained → cold-start fallback
# to peer-group or global model (or return 0, no signal).

def retrain_schedule() -> None   # daily rolling retrain per level/entity with enough data
```
Logic: per-entity model only when `count ≥ 20` (else fallback). IF output is a **supplementary signal** fused with rules; never the sole "malicious" judge.

## 4.5 Context Engine

**`context.py`**
```python
@dataclass
class ContextVector:
    who: str                       # entity_ref
    doing_what: str                # event_type + resource
    using_what: str                # source_entity/device
    from_where: str                # geo
    when: datetime                 # + hour_of_day_risk 0–1
    how_much: int                  # bytes
    target_sensitivity: float      # 0–1 from sensitivity tier mapping
    role_factor: float             # admin=1.0, exec=0.9, staff=0.6, contractor=0.8
    dept_factor: float             # 1.0 if in-scope else 1.4
    baseline_confidence: float     # LOW=0.4 MED=0.7 HIGH=1.0

def build(ev, profile_selected, user) -> ContextVector
```
Tier→sensitivity mapping: `public=0.1 internal=0.3 confidential=0.6 restricted=0.9`.

## 4.6 Risk Engine

**`risk.py`**
```python
def compute(anomaly: float, impact: float, confidence: float,
            rule_bonus: float = 0.0) -> Risk
    # risk_01 = anomaly * impact * confidence
    # risk_100 = min(1.0, risk_01 + min(rule_bonus, 0.15)) * 100
```
**The foundation is `Risk = Anomaly × Impact × Confidence` — never additive-capped.**

- `anomaly` = max(rule severities fused with IF signal): `anomaly = 0.7*max(rule_sev) + 0.3*ml_score`.
- `impact` = `max(target_sensitivity, role_factor)` blended with dept_factor.
- `confidence` = profile baseline confidence (LOW weight 0.4), so new/sparse entities are scored gently.
- Bands: `0–24 Low · 25–49 Medium · 50–74 High · 75–100 Critical`.
- Output includes a **breakdown dict** `{anomaly, impact, confidence, components:{rules:[...], ml:score}}` — the dashboard's "why flagged" payload.

## 4.7 Correlation Engine

**`correlation.py`**
```python
def resolve_chain(ev) -> list[str]      # [actor, source_entity, target_entity, peer_entity] non-empty for graph links

def cluster_for_entity(entity_ref: str, window_events: list[NormalizedEvent],
                       open_incidents: list[Incident]) -> Incident | None
# Groups triggered rules/alerts within a rolling window (e.g. 30 min);
# if >=2 chain connections across the entities or a Critical single alert → create/escalate incident.
def maintain_incident(inc: Incident, ev): ...   # append evidence, recompute chain + max risk
```
Logic ("these four entities and eight events form one suspicious chain"): correlation crosses entity boundaries via the shared chain — the Account Compromise sequence folds into a single incident because consecutive events share `actor` and `peer_entity`/`target_entity` edges within minutes.

## 4.8 Runner (pipeline orchestrator)

**`runner.py`**
```python
class AnalyticsRunner:
    def __init__(self, consumer: EngineConsumer, store: DAO): ...
    def on_event(self, ev: dict) -> None
        # validate → resolve_user → accumulate window → (on window close) →
        # build/select baseline (cold start) → rules evaluate → ml score →
        # build context → compute risk → correlation → persist alerts/incidents
    def run(self) -> None   # binds to EngineConsumer handler
def cron():                 # daily: rolling_retrain all + ml.retrain_schedule
```

## Flows
Single consumed event: validate → resolve → accumulate bucket. Bucket close (each hour) → baseline vs window (three levels) → rules + IF → context vector → risk join → correlation → upsert alert/incident → commit offset.

## Exit criteria (Phase 4)
- Each of the 5 canonical anomalies is detected with a **human-readable explanation** and a **defensible risk score** within ~1 window of occurrence.
- Sparse/new entities never produce LOW-confidence harsh judgements (cold-start verified in tests with 2-hour-history synthetic user).
- Multi-stage chain folds into one incident.

## Connect to next phase
Alerts/incidents rows feed the Phase 5 lifecycle + response engine.

---

# PHASE 5 — ALERT / INCIDENT / RESPONSE ENGINE

## Goal
Turn risk output into managed alerts and incidents with a team workflow and simulated response actions.

## Modules (package `analytics/lifecycle.py`, `analytics/response.py`)

### Escalation logic
```python
def escalate(risk: Risk, profile) -> tuple[alert_level, incident_needed]
# band + entity sensitivity decide:
#   Critical always incident · High + restricted-sensitivity → incident
#   High otherwise → alert·assigned · Medium/Low → open alert (triage)
```

### Alert/Incident lifecycle state machines
```
ALERT:   open → assigned → closed | escalated → incident
INCIDENT: open → assigned → investigating → resolved | false_positive
```
Each transition writes `updated_at` + `updated_by`.

```python
def create_alert(entity_ref, risk, evidence) -> Alert
def assign(obj, analyst_id) -> None
def escalate(alert_id) -> Incident
def close(obj, verdict) -> None          # resolved | false_positive
def add_note(incident_id, analyst_id, text) -> None
def role_can(role: str, action: str) -> bool   # RBAC guard, shared by API
```

### Response engine (`analytics/response.py`)
```python
PLAYBOOK: dict[alert_type, list[action]] = {
    "impossible_travel": ["force_mfa", "revoke_session"],
    "volume_spike":      ["restrict_access", "notify_manager"],
    "out_of_scope":      ["revoke_session", "restrict_access"],
    "dormant":           ["force_mfa", "notify_manager"],
    "novel_peer":        ["isolate_device", "investigate"],
    "chain":             ["force_mfa", "revoke_session", "isolate_device"],
}
def recommend(alert_type: str) -> list[str]
def apply(incident_id, action: str, actor: str) -> analyst_actions.row
# SIMULATED: no real sysadmin integration. Records action, status="applied(simulated)",
# impact summary {what changed, target, ts}; optional side-effect: marks entity "isolated"
# in a `simulated_state` JSONB so the dashboard shows its consequence.
```
Response actions are always framed as **recommended / simulated** — real enforcement is out of scope.

## Flows
Risk result → escalation → alert/incident rows → analyst sees → recommends actions → applies (audited) → closes with verdict.

## Exit criteria
High/Critical behaviors escalate correctly; each alert type has a playbook; all analyst actions land in `analyst_actions`.

## Connect to next phase
`analyst_actions` + incident state are the read models the Phase 6 API exposes.

---

# PHASE 6 — API LAYER (FastAPI)

## Goal
REST API with JWT auth + RBAC that serves the dashboard and accepts analyst/admin actions.

## Key components (package `api/`)

```
api/
  main.py          # FastAPI app, routers wiring, CORS
  auth.py          # JWT issue/verify, passwords, dependency get_current_user
  dependencies.py  # role guards
  routers/
    auth.py        # login, refresh
    overview.py    # GET /overview
    entities.py    # GET /users, /entities, /users/{id}/risk, /entities/{id}/risk
    alerts.py      # GET /alerts, PATCH /alerts/{id}
    incidents.py   # GET /incidents, PATCH /incidents/{id},
                   # POST /incidents/{id}/actions, /notes, GET /incidents/{id}/evidence
    admin.py       # POST /admin/users, PUT /admin/thresholds
```

### Authentication & RBAC
```python
def issue_token(user: UserAccount) -> str     # JWT HS256, exp 30 min, role claim
def require_role(*roles: str) -> dependency   # rejects if user.disabled or role mismatch
```
- **Analyst:** overview, entities, alerts, incidents, actions, notes.
- **Admin:** analyst/account management + thresholds tuning. Thresholds stored in `settings` table, read by engine risk at invocation (tunable without redeploy).

### Endpoint contracts (method · path · request → response)
| Method | Path | Request | Response | Roles |
|---|---|---|---|---|
| POST | `/auth/login` | `{username, password}` | `{access, refresh}` | all |
| POST | `/auth/refresh` | `{refresh}` | `{access}` | all |
| GET | `/overview` | – | `{total_risk, by_band:{...}, top_users:[...], top_entities:[...], open_alerts, open_incidents}` | analyst,admin |
| GET | `/users` | `?search=&dept=` | `[user_summary+risk]` | analyst,admin |
| GET | `/users/{id}/risk` | – | `{current, history:[{ts,risk}], explanation, baseline_snapshot}` | analyst,admin |
| GET | `/entities/{id}/risk` | – | same shape for device/server | analyst,admin |
| GET | `/alerts` | `?status=&band=` | `[alert]` | analyst,admin |
| PATCH | `/alerts/{id}` | `{status, assignee}` | `alert` | analyst,admin |
| GET | `/incidents` | `?status=&assignee=` | `[incident+chain]` | analyst,admin |
| PATCH | `/incidents/{id}` | `{status, assignee}` | `incident` | analyst,admin |
| GET | `/incidents/{id}/evidence` | – | `[raw_event, ...]` (contributing events) | analyst,admin |
| POST | `/incidents/{id}/actions` | `{action}` | `analyst_action` | analyst,admin |
| POST | `/incidents/{id}/notes` | `{text}` | `note` | analyst,admin |
| POST | `/admin/users` | `{username, role, password}` | `created_user` | admin |
| PUT | `/admin/thresholds` | `{k, dormancy_days, band_critical}` | `settings` | admin |

Response models are pydantic `BaseModel`s; errors use HTTP semantics (401 auth, 403 role, 404 missing, 422 validation). OpenAPI at `/docs`.

## Flows
Login → JWT → guarded routes → DAO reads/writes → JSON response. Evidence replay pulls `raw_events` by ids stored on the incident.

## Exit criteria
Role separation enforced (analyst cannot call admin endpoints); evidence replay returns full event bodies; `/docs` describes every endpoint.

## Connect to next phase
The dashboard consumes these contracts 1:1 in Phase 7.

---

# PHASE 7 — MULTI-USER SOC DASHBOARD (React + TypeScript)

## Goal
Role-aware UI with live risk/alert visuals; the **Entity Investigation** page is the flagship.

## Routing & structure
```
dashboard/src/
  main.tsx
  App.tsx                    # router + auth provider + theme
  lib/
    api.ts                   # typed fetch client (baseUrl, token refresh, error mapping)
    auth.tsx                 # auth context (token, user, login/logout, role guards)
    ws.ts                    # live event subscription (alerts push)
  features/
    auth/            Login, ProtectedRoute
    overview/        Overview
    entities/        UsersList, EntitiesList, RiskDrillDown
    investigate/     EntityInvestigation      ★ flagship
    alerts/          AlertQueue
    incidents/       IncidentList, IncidentDetail
    admin/           ManageUsers, Thresholds
  components/        RiskBadge, WhyFlaggedCard, Timeline, EvidenceList, ActionButtons, Nav
```

### Auth flow
`Login` → `POST /auth/login` → store tokens → `AuthProvider` exposes `{user, role, login, logout}`. `ProtectedRoute` checks token + role; stale token → refresh call → 401 → redirect login.

### Data flow (per screen)
- **Overview:** on mount `GET /overview`; live push via `ws.ts` updates counters; poll risk every 15 s.
- **Users/Entities:** `GET /users | /entities` + search; row click → drill-down.
- **Entity Investigation (flagship):** `GET /entities/{id}/risk` →
  renders in order: `Normal behavior (baseline snapshot) → Current behavior (recent windows) → Deviation (feature diffs) → Reason (WhyFlaggedCard) → Risk (RiskBadge + band) → Timeline (event stream) → Incidents (linked)`.
  `WhyFlaggedCard` renders the risk engine's `breakdown`: each rule's `explanation`, ML score, and impact/confidence components.
- **Alerts/Incidents:** `GET /alerts | /incidents` filters; `IncidentDetail` calls `/evidence`, `/actions`, `/notes`; buttons call `PATCH` + `POST` and optimistically update.
- **Admin:** role-gated routes; `POST /admin/users`, `PUT /admin/thresholds`.

### Live mechanism
`ws.ts` subscribes to a lightweight endpoint (WebSocket or SSE) that pushes newly created alert/incident ids from the runner; the client refetches the affected lists — no page refresh needed.

## Key components (props → purpose)
- `RiskBadge({ risk })` → colored band badge.
- `WhyFlaggedCard({ breakdown })` → renders rule explanations + component scores.
- `Timeline({ events })` → chronological event strip with severity icons.
- `EvidenceList({ eventIds })` → `GET /incidents/{id}/evidence` bodies.

## Scope discipline
Exactly the listed screens. **No feature creep** — Entity Investigation is where the demo wins.

## Exit criteria
Analyst + admin log in under separate accounts; analyst works an incident end-to-end (assign → review evidence → apply action → close); new simulated anomalies surface live without refresh.

## Connect to next phase
Dashboard is the window onto real (agent) data in Phase 8.

---

# PHASE 8 — WINDOWS AGENT (SECONDARY SOURCE — MUST NOT BLOCK)

## Goal
Real endpoint collection proving "these events aren't only synthetic". Built **only after** engine + dashboard work on the simulator.

## Modules (package `agents/windows_agent/`)
```
windows_agent/
  main.py             # service entrypoint, runs readers on threads, backoff
  readers/
    security_log.py   # Security event 4624/4625/4672 via wevtutil/win32evtlog
    sysmon.py         # Sysmon (if installed) event parsing
    file_watcher.py   # ReadDirectoryChangesW / watchdog file events
    usb.py            # PnP device change events
    process.py        # process create/terminate (optional, best-effort)
  normalize.py        # source → Common Event Schema
  batch.py            # buffering + flush to producer
```

### Reader logic (each is a poller thread)
- **security_log:** query last 60 s of events; map to `login`/`failure`/`privilege`; extract user, ip.
- **sysmon:** parse XML events (network, file, process) → map to schema.
- **file_watcher:** monitor configured directories; produce `file_access` with path + sizes.
- **usb:** PnP arrivals/departures → `usb` device-events.
- If a reader is unavailable (no permission, no Sysmon) → disable gracefully, log once, keep running others (fail-open design).

### Normalization + pipeline
```python
def to_schema(raw: dict) -> NormalizedEvent | None     # shared schema code, same as simulator
def run_batch(events: list[NormalizedEvent]) -> None
# Buffers up to N events or T seconds → EventProducer.send each (idempotent)
# Retry with backoff on Kafka outage; never lose on crash window beyond buffer.
```
Config: `agent.toml`/env — directories to watch, poll interval, kafka bootstrap, which readers are enabled.

## Flows
Reader → raw → `to_schema` → buffer → producer → Kafka → same engine as simulator data.

## Exit criteria
On a real Windows box (with/without Sysmon), the agent streams authentic login/file/USB events the engine understands; readers degrade gracefully if privileges limit them.

## Connect to next phase
Real events exercise the full pipeline, feeding the Phase 9 evaluation/demo (mixed synthetic + real).

---

# PHASE 9 — EVALUATION, DEMO, TESTS, RUNBOOK

## Goal
Prove the system works with measurable numbers and a reproducible demo.

## 9.1 Metrics (simulator = ground truth)
From `ground_truth` vs detected alerts/incidents:

| Metric | Definition | Source |
|---|---|---|
| Recall (detection rate) | detected/total planted anomalies | ground_truth vs alerts |
| Precision | true/false positives | alerts vs ground_truth |
| F1 | harmonic mean | computed |
| False Positive Rate | normal events flagged/all normal | window-level |
| Detection latency | alert_created − anomaly_start (seconds) | timestamps |
| Correlation accuracy | chains collapsed to 1 incident (not N alerts) | incident chains |

```python
# tests/eval/metrics.py
def evaluate(detected: list[Alert], truth: list[GroundTruth]) -> Metrics | None
def report(metrics) -> str     # Markdown table for docs/demo
```

## 9.2 Ablation study (technical slide)
```
30 days normal (sim) → build baseline → Day 31 inject anomalies →
measure under three configs:
  A. baseline-only rules (low-risk statistical checks)
  B. baseline + heuristic rule engine (full rules)
  C. baseline + rules + Isolation Forest
Compare Recall/Precision/FPR across A/B/C.
```
Runner: `tests/eval/ablation.py` runs the engine 3× on the same data with feature flags (`ENABLE_RULES`, `ENABLE_ML`), persists results.

## 9.3 Demo runbook (10 min)
Steps: `docker compose up` → `make seed` → `make backfill-demo` → `make live-demo` with scheduled planted anomalies for all five cases + one compromise chain. For each, expected dashboard states, WhyFlaggedCard text, risk values, action applied, incident outcome. Include a mixed real+sim show of the Windows agent (Phase 8).

## 9.4 Test suites
- **Unit:** `tests/unit/` — features accumulation, each rule detector (table-driven), confidence/cold-start, risk formula (fixed inputs → expected bands), correlation chain folding.
- **Contract:** `tests/contract/` — Common Schema validation; agents/simulator produce schema-valid events.
- **Integration:** `tests/integration/` — full pipeline: simulator → Kafka → engine → alert/incident → API → fetch (assert every hop).
- **Dashboard:** build + lint + typecheck (`npm run build`, `tsc --noEmit`).
- **API:** pytest with FastAPI TestClient (routes, RBAC, evidence replay).

## Exit criteria
Pipeline runs from one command; evaluation metrics print; ablation table renders; a fresh reviewer reproduces the demo and sees every anomaly detected, explained, scored, correlated, acted upon.

## Connect to next phase
Phase 10 (stretch) — Tier-2 anomaly rules and optional blockchain audit module build on this same engine + evidence layer.

---

# PHASE 10 — STRETCH (NOT part of checkpoint +1)

- **Tier-2 rules:** new device, privilege escalation, USB transfer, external cloud upload, config-change, unusual app usage — each a new module in `rules/` reusing the baseline/context/risk pipeline.
- **Blockchain audit module (optional):** `Incident → SHA-256 of evidence bundle → Hyperledger Fabric transaction → audit verification endpoint`. Independent; added only if evaluation strongly expects it — preserves the evidence trail in Postgres as the source of truth.

---

## Cross-Phase Data Flow (how everything ties together)

```
agent/simulator ──normalized JSON──▶ Kafka topics
                                        │ (partition by entity_id)
                                        ▼
        Processor(validate) → FeatureEngine(accumulate hour)
                                        ▼
                 BaselineEngine: individual ▸ peer-group ▸ global (cold-start aware)
                                        ▼
                RuleEngine(deterministic 5 cases) ∥ IsolationForest(supplementary)
                                        ▼
                          ContextVector → Risk=Anomaly×Impact×Confidence → 0–100
                                        ▼
                  CorrelationEngine (entity-chain) → Incident with evidence links
                                        ▼
        Postgres: alerts / incidents / analyst_actions / evidence   ←─ FastAPI
                                        ▼
                 React SOC dashboard (multi-user, roles) → Entity Investigation
                                        ▼
              metrics (Recall/Precision/F1/FPR/latency) vs ground_truth
```

## Global Exit Criteria (whole project)
1. One command boots everything.
2. All five canonical anomalies + a compromise chain are detected live, explained, risk-scored (Anomaly×Impact×Confidence), correlated into incidents.
3. Metrics + ablation results documented.
4. Windows agent streams real events the same engine understands.
5. A fresh reviewer reproduces the full demo from the README runbook.

---

*Document end — LLD covers Phases 0 through 9 in depth; Phase 10 noted as stretch. Implementation proceeds phase-by-phase from this document.*