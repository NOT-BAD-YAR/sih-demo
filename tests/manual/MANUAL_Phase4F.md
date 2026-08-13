# MANUAL VERIFICATION — Phase 4F (Runner Orchestration)

> Run by BOTH the builder and the user independently. Phase 4F is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (350 unit/structure/contract + 67 integration — 417 total)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Postgres/Kafka run in containers for the DB-backed checks)
- [ ] Python venv at `.venv` with `scikit-learn` + `numpy` installed
- [ ] `.env` present (or `POSTGRES_DSN` exported)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d
# wait until `docker compose ps` shows BOTH postgres and kafka healthy (~60-90 s)
```

## 3. Step-by-step checks

### 3.1 Runner API surface
- Run: `& $py -c "from analytics.runner import AnalyticsRunner, cron, ML_ONLY_THRESHOLD; print('runner apis ok, ML_ONLY_THRESHOLD =', ML_ONLY_THRESHOLD)"`
- Expect: `runner apis ok, ML_ONLY_THRESHOLD = 0.5`.
- [ ] confirmed

### 3.2 Ingest mechanics — valid events accumulate, invalid payloads are dropped, flush closes windows
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone, timedelta; from analytics.runner import AnalyticsRunner; \
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc); \
  from simulator.schema import build_event; from streaming.producer import normalize_payload; \
  def w(eid, ts): return normalize_payload(build_event(entity_type='user', entity_id='u1', user_id='u1', event_type='login', actor='u1', ts=ts, event_id=eid)); \
  r = AnalyticsRunner(); \
  print('valid accepted:', r.on_event(w('e1', now)) is not None); \
  print('invalid dropped:', r.on_event({}) is None, '| dropped stat:', r.stats['dropped']); \
  r.on_event(w('e2', now + timedelta(hours=1))); \
  print('bucket rollover closes window:', r.stats['windows_closed'] == 1); \
  r.on_event(w('e3', now + timedelta(hours=2))); \
  print('flush closes open window:', r.flush() == 1)"
  ```
- Expect: `valid accepted: True`, `invalid dropped: True | dropped stat: 1`, `bucket rollover closes window: True`, `flush closes open window: True`.
- [ ] confirmed

### 3.3 THE exit criterion — Account Compromise folds into ONE incident through the runner
- Run:
  ```powershell
  & $py -c @"
  import random
  from datetime import datetime, timezone
  from collections import defaultdict
  from simulator.org import generate_org
  from simulator.engine import run_backfill
  from simulator.anomaly import inject_scenario
  from streaming.producer import normalize_payload
  from analytics.processor import validate
  from analytics.features import accumulate_all, finalize
  from analytics.ml import train, clear_models
  from analytics.runner import AnalyticsRunner

  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=30, events_per_day=12, seed=42)) if n]
  by_entity = defaultdict(list)
  for w in accumulate_all(back).values():
      by_entity[w.entity_ref].append(finalize(w))
  clear_models()
  train('global','__global__', [v for vs in by_entity.values() for v in vs], force=True)

  runner = AnalyticsRunner(org=org, history=dict(by_entity))
  for e in inject_scenario(org, random.Random(7), 'compromise_chain', now):
      runner.on_event(normalize_payload(e))
  runner.flush()
  inc = runner._open_incidents[0]
  print('incidents:', len(runner._open_incidents), '| risk=', inc.risk, '[' + inc.severity + ']')
  print('evidence refs (' + str(len(inc.evidence_refs)) + '):', sorted(inc.evidence_refs))
  print('entity chain:', sorted(inc.entity_chain))
  print('timeline span:', int((inc.updated_at - inc.created_at).total_seconds()//60), 'min')
  "@
  ```
- Expect: `incidents: 1`, `risk=90 [Critical]`, `evidence refs (3): ...`, chain contains the actor + `THREAT-DEVICE` + `STORAGE.EXTERNAL.CLOUD`, `timeline span: 11 min`. Five planted events become ONE incident.
- [ ] confirmed

### 3.4 Cold start is gentle — an unknown entity is never judged harshly
- Run:
  ```powershell
  & $py -c @"
  from datetime import datetime, timezone
  from simulator.org import generate_org
  from simulator.schema import build_event
  from streaming.producer import normalize_payload
  from analytics.runner import AnalyticsRunner
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  srv = org.servers[0]
  ev = normalize_payload(build_event(entity_type='server', entity_id=srv.server_id, actor=srv.server_id,
      source_entity=srv.server_id, target_entity='crm_db', event_type='network_conn',
      peer_entity='UNKNOWN-999', event_id='np-1', ts=now, sensitivity='confidential'))
  runner = AnalyticsRunner(org=org)   # no history at all for this server
  runner.on_event(ev); runner.flush()
  inc = runner._open_incidents[0]
  print('cold-start risk:', inc.risk, '[' + inc.severity + '] (must be <= 50, never High/Critical)')
  "@
  ```
- Expect: `cold-start risk: 26 [Medium] (must be <= 50, never High/Critical)`.
- [ ] confirmed

### 3.5 Dormant account activation is detected when a learned window exists
- Run:
  ```powershell
  & $py -c @"
  from datetime import datetime, timezone, timedelta
  from simulator.org import generate_org
  from simulator.schema import build_event
  from streaming.producer import normalize_payload
  from analytics.runner import AnalyticsRunner
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  emp = next(e for e in org.employees if e.dormant)
  hist = [{'entity_ref': emp.emp_id, 'window_start': (now - timedelta(days=30-i)).isoformat(),
           'volume': 50000000, 'event_count': 12, 'active_hours_frac': 0.5,
           'unique_peers': ['SRV-01'], 'new_peer_count': 0, 'location_count': 1,
           'location_dist_km': 0.0, 'dept_distinct': [emp.department],
           'sensitivity_hist': {'internal': 12}, 'fail_rate': 0.0, 'staleness_days': 0}
          for i in range(30)]
  ev = normalize_payload(build_event(entity_type='user', entity_id=emp.emp_id, user_id=emp.emp_id,
      event_type='login', actor=emp.emp_id, source_entity=emp.device_id, target_entity=emp.device_id,
      ts=now.replace(hour=2, minute=30), event_id='dorm-1'))
  runner = AnalyticsRunner(org=org, history={emp.emp_id: hist})
  runner.on_event(ev); runner.flush()
  inc = runner._open_incidents[0]
  print('dormant activation:', inc.risk, '[' + inc.severity + '] (Medium or High, never Low/Critical)')
  "@
  ```
- Expect: `dormant activation: 39 [Medium] (Medium or High, never Low/Critical)` (exact risk may vary a little).
- [ ] confirmed

### 3.6 Persistence — the runner writes alerts + the incident to the real tables
- Run:
  ```powershell
  & $py -c "import subprocess, sys, time; from pathlib import Path; \
  ROOT = Path.cwd(); subprocess.run(['docker','compose','up','-d','postgres'], cwd=ROOT); time.sleep(2); \
  subprocess.run([sys.executable,'-m','alembic','-c','db/alembic.ini','upgrade','head'], cwd=ROOT, check=True); \
  from db.conn import connect; from db.dao import get_incidents, get_windows; \
  conn = connect('postgresql://ueba:ueba_secret@localhost:5432/ueba'); \
  with conn.cursor() as cur: cur.execute('TRUNCATE alerts, incidents, feature_windows, behavioral_profiles RESTART IDENTITY CASCADE'); conn.commit(); \
  import random; from datetime import datetime, timezone; from collections import defaultdict; \
  from simulator.org import generate_org; from simulator.engine import run_backfill; \
  from simulator.anomaly import inject_scenario; from streaming.producer import normalize_payload; \
  from analytics.processor import validate; from analytics.features import accumulate_all, finalize; \
  from analytics.ml import train, clear_models; from analytics.runner import AnalyticsRunner; \
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc); org = generate_org(seed=42); \
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=30, events_per_day=12, seed=42)) if n]; \
  by_entity = defaultdict(list); \
  for w in accumulate_all(back).values(): by_entity[w.entity_ref].append(finalize(w)); \
  clear_models(); train('global','__global__', [v for vs in by_entity.values() for v in vs], force=True); \
  runner = AnalyticsRunner(store=conn, org=org, history=dict(by_entity)); \
  for e in inject_scenario(org, random.Random(7), 'compromise_chain', now): runner.on_event(normalize_payload(e)); \
  runner.flush(); \
  rows = get_incidents(conn, status='open'); \
  print('runner stats:', runner.stats['incidents'], 'incident(s),', runner.stats['alerts'], 'alert(s)'); \
  r = rows[0]; print('DB incident:', r['risk'], r['severity'], 'evidence', len(r['evidence_refs']), 'chain', sorted(r['entity_chain'])); \
  print('feature windows persisted:', len(get_windows(conn, r['entity_ref'])) >= 1); \
  conn.close()"
  ```
- Expect: `runner stats: 1 incident(s), 3 alert(s)`, `DB incident: 90 Critical evidence 3 chain ['...', 'THREAT-DEVICE', 'STORAGE.EXTERNAL.CLOUD']`, `feature windows persisted: True`. The runner alone (Kafka-optional) drives ingest → windowing → scoring → alert/incident persistence.
- [ ] confirmed

### 3.7 cron() — daily retrain rebuilds baselines from stored windows
- Run:
  ```powershell
  & $py -c @"
  import subprocess, sys, time, random
  from pathlib import Path
  ROOT = Path.cwd(); subprocess.run(['docker','compose','up','-d','postgres'], cwd=ROOT); time.sleep(2)
  subprocess.run([sys.executable,'-m','alembic','-c','db/alembic.ini','upgrade','head'], cwd=ROOT, check=True)
  from db.conn import connect
  from db.dao import upsert_window, get_profile
  conn = connect('postgresql://ueba:ueba_secret@localhost:5432/ueba')
  from datetime import datetime, timezone, timedelta
  from simulator.org import generate_org
  from analytics.runner import cron
  from analytics.ml import train as ml_train
  from collections import defaultdict
  from simulator.engine import run_backfill
  from streaming.producer import normalize_payload
  from analytics.processor import validate
  from analytics.features import accumulate_all, finalize
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=14, events_per_day=12, seed=42)) if n]
  by_entity = defaultdict(list)
  for w in accumulate_all(back).values(): by_entity[w.entity_ref].append(finalize(w))
  emp = org.employees[0]
  for w in by_entity[emp.emp_id][:30]:
      upsert_window(conn, emp.emp_id, datetime.fromisoformat(w['window_start']), w)
  ml_train('individual', emp.emp_id, by_entity[emp.emp_id][:30], force=True)
  summary = cron(conn, org, [emp.emp_id], last_n_days=30)
  ind = get_profile(conn, emp.emp_id, 'individual'); g = get_profile(conn, emp.emp_id, 'global')
  print('cron summary:', summary)
  print('individual baseline:', bool(ind and ind.get('feature_stats')))
  print('global baseline:', bool(g and g.get('feature_stats')))
  conn.close()
  "@
  ```
- Expect: `cron summary: {'individuals': 1, 'peer_groups': 1, 'global': True, 'ml_retrained': ['individual:EMP0xx']}`, `individual baseline: True`, `global baseline: True`.
- [ ] confirmed

### 3.8 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `350 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `67 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.8 confirmed by builder AND user
- [ ] `on_event` accepts wire payloads; invalid payloads are dropped without crashing
- [ ] Windows close on hour-bucket rollover and at `flush()`; `run()` needs a bound consumer
- [ ] A closed window is scored only when a rule fires OR the ML signal is strong (>= 0.5)
- [ ] The multi-stage Account Compromise folds into ONE incident through the runner
- [ ] Cold-start entities (no baseline) are never judged harshly (Low/Medium only)
- [ ] Dormant accounts fire when a learned active window exists (cold-start safety by design)
- [ ] Alerts + incidents persist through the runner to the real tables; `update_incident` round-trips escalation
- [ ] `cron()` rebuilds individual/peer-group/global baselines and retrains ML models
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase4f_diags.txt` — runner diagnostics: spike 90 Critical, chain folds into ONE incident (risk 90 Critical, 3 evidence refs), cold-start 26 Medium
- [ ] `docs/verify_phase4f_pytest_unit.txt` — pytest summary `350 passed`
- [ ] `docs/verify_phase4f_pytest_int.txt` — pytest summary `67 passed`
