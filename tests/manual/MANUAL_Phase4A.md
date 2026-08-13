# MANUAL VERIFICATION — Phase 4A (Behavioral Baselines)

> Run by BOTH the builder and the user independently. Phase 4A is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (194 unit/structure/contract + 38 integration — 232 total)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Postgres runs in a container for the DB checks)
- [ ] Python venv at `.venv` with `psycopg2`, `alembic`, `scikit-learn` installed
- [ ] `.env` present (or export `POSTGRES_DSN=postgresql://ueba:ueba_secret@localhost:5432/ueba`)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d
# wait until `docker compose ps` shows BOTH postgres and kafka healthy (~60-90 s)
```

## 3. Step-by-step checks

### 3.1 Migration 0002 applies (unique feature_windows upsert key)
- Run: `& $py -m alembic -c db/alembic.ini upgrade head`
- Expect: no error; `alembic history` lists `0001 -> 0002 (head)`.
- [ ] confirmed

### 3.2 Event Processor validates valid payloads and rejects garbage
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone; from simulator.schema import build_event; \
  from streaming.producer import normalize_payload; from analytics.processor import validate; \
  ev = build_event(entity_type='user', entity_id='EMP001', user_id='EMP001', event_type='login', \
  actor='EMP001', source_entity='LPT-001', target_entity='LPT-001', ts=datetime.now(timezone.utc)); \
  ok = validate(normalize_payload(ev)); \
  print('valid ->', ok.event_type, ok.entity_id); \
  bad = dict(normalize_payload(ev), event_type='teleport'); \
  print('invalid event_type ->', validate(bad))"
  ```
- Expect: `valid -> login EMP001` and `invalid event_type -> None` — bad input is dropped, never crashes.
- [ ] confirmed

### 3.3 Device events resolve to owning user (resolve_user)
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone; from simulator.schema import build_event; \
  from streaming.producer import normalize_payload; from analytics.processor import validate, resolve_user; \
  ev = build_event(entity_type='device', entity_id='LPT-001', user_id='', event_type='usb', \
  actor='LPT-001', ts=datetime.now(timezone.utc)); \
  n = validate(normalize_payload(ev)); \
  print('owner:', resolve_user(n, {'LPT-001': 'EMP007'})); \
  print('unknown device:', repr(resolve_user(n, {})))"
  ```
- Expect: `owner: EMP007` and `unknown device: ''` — no mapping → gently empty (cold start).
- [ ] confirmed

### 3.4 Feature accumulation: hour-bucketed windows with volume/peers/location
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone, timedelta; from simulator.schema import build_event; \
  from streaming.producer import normalize_payload; from analytics.processor import validate; \
  from analytics.features import accumulate_all, finalize; \
  evs = [validate(normalize_payload(build_event(entity_type='user', entity_id='EMP001', user_id='EMP001', \
  event_type='download', actor='EMP001', source_entity='LPT-001', target_entity='git', ts=datetime(2026,1,1,9,5,tzinfo=timezone.utc), \
  bytes_moved=1048576, peer_entity='SRV-01', file_path='/Developers/git/a.zip'))), \
  validate(normalize_payload(build_event(entity_type='user', entity_id='EMP001', user_id='EMP001', \
  event_type='download', actor='EMP001', source_entity='LPT-001', target_entity='git', ts=datetime(2026,1,1,9,35,tzinfo=timezone.utc), \
  bytes_moved=2097152, peer_entity='SRV-01', file_path='/Developers/git/b.zip')))]; \
  wins = accumulate_all(evs); \
  for key, w in wins.items(): \
      v = finalize(w); print(key, '| volume:', v['volume'], '| count:', v['event_count'], '| active_frac:', v['active_hours_frac'])"
  ```
- Expect: one window keyed `EMP001@2026-01-01T09:00:00+00:00` with `volume: 3145728`, `count: 2`, `active_frac: 0.5`.
- [ ] confirmed

### 3.5 Baseline builder: confidence thresholds (LOW <20 · MED 20–100 · HIGH >100)
- Run: `& $py -c "from analytics.baseline import confidence_for; print(confidence_for(5), confidence_for(50), confidence_for(500))"`
- Expect: `LOW MED HIGH`.
- [ ] confirmed

### 3.6 Individual + peer-group + global baselines build from simulator backfill
- Run:
  ```powershell
  & $py -c "from simulator.org import generate_org; from simulator.engine import run_backfill; \
  from streaming.producer import normalize_payload; from analytics.processor import validate; \
  from analytics.features import accumulate_all, finalize; from analytics.baseline import build_individual, build_peer_group, build_global; \
  from collections import defaultdict; \
  org = generate_org(seed=42); events = run_backfill(org, days=30, events_per_day=12, seed=42); \
  norm = [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]; \
  by_entity = defaultdict(list); \
  for w in accumulate_all(norm).values(): by_entity[w.entity_ref].append(finalize(w)); \
  ind = [build_individual(r, v) for r, v in by_entity.items()]; \
  peer = build_peer_group('HR', ind[:20]); glb = build_global(ind); \
  print('individual profiles:', len(ind)); \
  print('peer HR count:', peer['_count'], '| global count:', glb['_count']); \
  print('sample volume mean:', round(ind[0]['feature_stats']['volume']['mean'], 1))"
  ```
- Expect: `individual profiles: <N>` (near 97), `peer HR count: <large>`, `global count: <~15000>`, and a non-zero `sample volume mean`. Baselines build from real seeded data.
- [ ] confirmed

### 3.7 Cold-start fallback: sparse → peer_group, rich → individual
- Run:
  ```powershell
  & $py -c "from simulator.org import generate_org; from simulator.engine import run_backfill; \
  from streaming.producer import normalize_payload; from analytics.processor import validate; \
  from analytics.features import accumulate_all, finalize; from analytics.baseline import build_individual, build_peer_group, build_global, select_level; \
  from collections import defaultdict; \
  org = generate_org(seed=42); events = run_backfill(org, days=30, events_per_day=12, seed=42); \
  norm = [n for n in (validate(normalize_payload(e)) for e in events) if n is not None]; \
  by_entity = defaultdict(list); \
  for w in accumulate_all(norm).values(): by_entity[w.entity_ref].append(finalize(w)); \
  ind = [build_individual(r, v) for r, v in by_entity.items()]; \
  peer = build_peer_group('HR', ind[:20]); glb = build_global(ind); \
  first = list(by_entity.keys())[0]; \
  sparse = build_individual('EMP999', by_entity[first][:3]); \
  rich = build_individual(first, by_entity[first]); \
  print('cold-start sparse ->', select_level('EMP999', {'individual': sparse, 'peer_group': peer, 'global': glb})[0]); \
  print('cold-start rich   ->', select_level(first, {'individual': rich, 'peer_group': peer, 'global': glb})[0])"
  ```
- Expect: `cold-start sparse -> peer_group` and `cold-start rich -> individual`. A 3-window entity is judged gently via its peer group; a 100+ window entity uses its own baseline.
- [ ] confirmed

### 3.8 Window + profile DAOs persist to Postgres (real DB)
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone; from db.conn import connect; \
  from db.dao import upsert_window, get_windows, upsert_profile, get_profile; \
  c = connect(); ts = datetime(2026,1,1,9,0,tzinfo=timezone.utc); \
  vec = {'entity_ref':'EMP001','window_start':ts.isoformat(),'volume':1000,'event_count':5,'active_hours_frac':0.5, \
  'unique_peers':['SRV-01'],'new_peer_count':0,'location_count':1,'location_dist_km':0.0, \
  'dept_distinct':['HR'],'sensitivity_hist':{'internal':5},'fail_rate':0.0,'staleness_days':0}; \
  upsert_window(c, 'EMP001', ts, vec); \
  print('windows:', len(get_windows(c, 'EMP001'))); \
  upsert_profile(c, {'entity_ref':'EMP001','level':'individual','feature_stats':{'volume':{'mean':1000,'std':10,'count':20,'confidence':'MED'}}, \
  'allowed_sets':{'peers':['SRV-01']},'active_window':{'start_hour':9,'end_hour':18},'confidence':'MED','updated_to':datetime.now(timezone.utc)}); \
  p = get_profile(c, 'EMP001', 'individual'); \
  print('profile confidence:', p['confidence']); c.close()"
  ```
- Expect: `windows: 1` and `profile confidence: MED` — the engine's persisted read/write path works.
- [ ] confirmed

### 3.9 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `194 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `38 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.9 confirmed by builder AND user
- [ ] Migration 0002 applies cleanly; `feature_windows` upsert is idempotent per bucket
- [ ] Event Processor accepts valid events, drops invalid ones without crashing
- [ ] Feature Engine builds hour-bucketed windows with correct volume/peers/location/sensitivity
- [ ] Confidence thresholds correct (LOW/MED/HIGH at 5/50/500)
- [ ] Three-level baselines build from seeded org data
- [ ] Cold start verified: sparse → peer_group, rich → individual
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase4a_diags.txt` — outputs of 3.2–3.8
- [ ] `docs/verify_phase4a_pytest_unit.txt` — pytest summary `194 passed`
- [ ] `docs/verify_phase4a_pytest_int.txt` — pytest summary `38 passed`
- [ ] `docs/verify_phase4a_migrations.txt` — `alembic history` / upgrade output
