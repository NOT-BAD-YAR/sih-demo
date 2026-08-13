# MANUAL VERIFICATION — Phase 5 (Alert · Incident · Response Engine)

> Run by BOTH the builder and the user independently. Phase 5 is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (400 unit/structure/contract + 74 integration — 474 total)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Postgres runs in a container for the DB-backed checks)
- [ ] Python venv at `.venv` with `scikit-learn` + `numpy` installed
- [ ] `.env` present (or `POSTGRES_DSN` exported)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d postgres
# wait until `docker compose ps` shows postgres healthy (~30-60 s)
```

## 3. Step-by-step checks

### 3.1 API surface
- Run: `& $py -c "from analytics.lifecycle import escalate, create_alert, to_incident, transition, assign, close, add_note, role_can; from analytics.response import PLAYBOOK, ACTIONS, recommend, simulate; print('phase5 apis ok,', len(ACTIONS), 'actions,', len(PLAYBOOK), 'playbook rows')"`
- Expect: `phase5 apis ok, 6 actions, 6 playbook rows`.
- [ ] confirmed

### 3.2 Escalation tiering — Critical/restricted-High become incidents, High becomes an assigned alert, Medium/Low stay open alerts
- Run:
  ```powershell
  & $py -c @"
  from analytics.lifecycle import escalate
  for band in ('Low','Medium','High','Critical'):
      for sens in ('internal','restricted'):
          print(band, sens, '->', escalate(band, sens))
  "@
  ```
- Expect: `Critical` → `('incident', True)` for both sensitivities, `High restricted` → `('incident', True)`, `High internal` → `('assigned', False)`, `Low/Medium` → `('open', False)`.
- [ ] confirmed

### 3.3 THE exit criterion — a runner-produced chain incident is assigned, actioned per the playbook, and closed resolved, with every action audited
- Run:
  ```powershell
  & $py -c @"
  import subprocess, sys, time, random
  from pathlib import Path
  ROOT = Path.cwd(); subprocess.run(['docker','compose','up','-d','postgres'], cwd=ROOT); time.sleep(2)
  subprocess.run([sys.executable,'-m','alembic','-c','db/alembic.ini','upgrade','head'], cwd=ROOT, check=True)
  from db.conn import connect
  conn = connect('postgresql://ueba:ueba_secret@localhost:5432/ueba')
  with conn.cursor() as cur:
      cur.execute('TRUNCATE alerts, incidents, analyst_actions, feature_windows, behavioral_profiles RESTART IDENTITY CASCADE')
      conn.commit()
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
  from analytics.correlation import Incident
  from analytics.lifecycle import assign, investigate, close, add_note
  from analytics.response import apply, list_actions
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=30, events_per_day=12, seed=42)) if n]
  by_entity = defaultdict(list)
  for w in accumulate_all(back).values(): by_entity[w.entity_ref].append(finalize(w))
  clear_models(); train('global','__global__', [v for vs in by_entity.values() for v in vs], force=True)
  runner = AnalyticsRunner(store=conn, org=org, history=dict(by_entity))
  for e in inject_scenario(org, random.Random(7), 'compromise_chain', now): runner.on_event(normalize_payload(e))
  runner.flush()
  from db.dao import get_incidents, update_incident
  row = get_incidents(conn, status='open')[0]
  inc = Incident(id=row['id'], entity_ref=row['entity_ref'], severity=row['severity'], risk=row['risk'],
      status=row['status'], entity_chain=list(row['entity_chain']), evidence_refs=list(row['evidence_refs']),
      created_at=row['created_at'], updated_at=row['updated_at'])
  assign(inc, analyst_id='bob', actor='alice'); update_incident(conn, {**inc.row(), 'id': inc.id})
  investigate(inc, actor='bob'); update_incident(conn, {**inc.row(), 'id': inc.id})
  for a in ('force_mfa','revoke_session','isolate_device'):
      apply(conn, inc.id, a, actor='bob', alert_type='chain', entity_chain=inc.entity_chain)
  add_note(inc, 'bob', 'response complete, closing')
  close(inc, 'resolved', actor='bob'); update_incident(conn, {**inc.row(), 'id': inc.id})
  trail = list_actions(conn, incident_id=inc.id)
  final = get_incidents(conn)[0]
  print('incident:', final['status'], 'assigned_to', final['assigned_to'], 'updated_by', final['updated_by'])
  print('actions audited:', len(trail), sorted(a['action'] for a in trail))
  print('statuses:', sorted({a['status'] for a in trail}))
  print('simulated_state example:', trail[0]['simulated_state'])
  print('note:', final['notes']['entries'][0]['text'])
  conn.close()
  "@
  ```
- Expect: `incident: resolved assigned_to bob updated_by bob`, `actions audited: 3 ['force_mfa', 'isolate_device', 'revoke_session']`, `statuses: ['applied(simulated)']`, a JSONB `simulated_state` (newest action is `isolate_device`, e.g. `{'isolated_entity': ['EMP045', 'STORAGE.EXTERNAL.CLOUD', 'THREAT-DEVICE', 'hr_share']}`), `note: response complete, closing`. One audited workflow: detection → assign → investigate → simulated response → resolve.
- [ ] confirmed

### 3.4 Response playbook — every detection type maps to its recommended actions
- Run:
  ```powershell
  & $py -c "from analytics.response import PLAYBOOK; [print(k, '->', v) for k, v in sorted(PLAYBOOK.items())]"
  ```
- Expect: `chain -> ['force_mfa', 'revoke_session', 'isolate_device']`, `dormant -> ['force_mfa', 'notify_manager']`, `impossible_travel -> ['force_mfa', 'revoke_session']`, `novel_peer -> ['isolate_device', 'investigate']`, `out_of_scope -> ['revoke_session', 'restrict_access']`, `volume_spike -> ['restrict_access', 'notify_manager']`.
- [ ] confirmed

### 3.5 RBAC — analyst works the queue, admin manages
- Run: `& $py -c "from analytics.lifecycle import role_can; print('analyst.act', role_can('analyst','act'), '| analyst.manage', role_can('analyst','manage'), '| admin.tune', role_can('admin','tune_thresholds'))"`
- Expect: `analyst.act True | analyst.manage False | admin.tune True`.
- [ ] confirmed

### 3.6 Migration 0003 applied
- Run: `& $py -c "from db.dao import SCHEMA_TABLES; import subprocess, sys; subprocess.run([sys.executable,'-m','alembic','-c','db/alembic.ini','current'], check=True)"`
- Expect: alembic reports `0003_lifecycle_audit` as the current head.
- [ ] confirmed

### 3.7 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `400 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `74 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.7 confirmed by builder AND user
- [ ] Escalation tiering matches the LLD: Critical and restricted-High → incident; High → assigned alert; Medium/Low → open alert
- [ ] Incident lifecycle open → assigned → investigating → resolved|false_positive is enforced; every transition stamps `updated_at` + `updated_by`
- [ ] Alerts follow their own open → assigned → investigating → resolved|false_positive machine
- [ ] `analyst_actions` audit trail records every simulated response action with status `applied(simulated)` and JSONB `simulated_state`
- [ ] The response playbook covers all six detection types; unknown types degrade safely
- [ ] RBAC: analysts can work the queue, only admins manage/tune
- [ ] `updated_by`/`assigned_to` persist through `insert_incident`/`update_incident` and survive DB round-trips
- [ ] A runner-produced incident flows through assign → investigate → response → resolve with a matching audit trail
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase5_diags.txt` — escalation matrix, alert→incident, lifecycle, playbook, simulated side-effects, RBAC
- [ ] `docs/verify_phase5_pytest_unit.txt` — pytest summary `400 passed`
- [ ] `docs/verify_phase5_pytest_int.txt` — pytest summary `74 passed`