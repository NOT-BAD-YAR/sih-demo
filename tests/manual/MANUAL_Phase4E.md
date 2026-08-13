# MANUAL VERIFICATION — Phase 4E (Correlation Engine)

> Run by BOTH the builder and the user independently. Phase 4E is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (334 unit/structure/contract + 63 integration — 397 total)
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

### 3.1 Correlation module present, API surface
- Run: `& $py -c "from analytics.correlation import resolve_chain, cluster_for_entity, maintain_incident, score_event, ScoredEvent, Incident; print('correlation apis ok')"`
- Expect: `correlation apis ok`.
- [ ] confirmed

### 3.2 Chain extraction (resolve_chain)
- Run:
  ```powershell
  & $py -c "from analytics.correlation import resolve_chain; \
  print(resolve_chain({'actor':'u1','source_entity':'dev1','target_entity':'res1','peer_entity':''})); \
  print(resolve_chain({'actor':'u1','source_entity':'dev1','target_entity':'u1','peer_entity':'dev1'}))"
  ```
- Expect: `['u1', 'dev1', 'res1']` (empty peer filtered) then `['u1', 'dev1']` (deduped, order preserved).
- [ ] confirmed

### 3.3 Incident creation gate
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone; from analytics.correlation import cluster_for_entity, ScoredEvent; \
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc); \
  def se(eid, ref, risk, chain): return ScoredEvent(eid, ref, now, risk, 0.5, list(chain), f'A-{eid}'); \
  print('one entity, low risk    ->', cluster_for_entity('u1', [se('e1','u1',30,['u1'])], [])); \
  print('chain of 2 entities     ->', cluster_for_entity('u1', [se('e2','u1',40,['u1','dev1'])], []) is not None); \
  print('single Critical event   ->', cluster_for_entity('u1', [se('e3','u1',80,['u1'])], []).severity)"
  ```
- Expect: `None`, `True`, `Critical`. An incident needs a chain spanning >= 2 entities OR a single Critical (>=75) event.
- [ ] confirmed

### 3.4 Cross-entity escalation — new event folds into the open incident
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone, timedelta; from analytics.correlation import cluster_for_entity, ScoredEvent; \
  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc); \
  a = ScoredEvent('e1','emp1',now,65.0,0.7,['emp1','THREAT-DEVICE'],'A1'); \
  inc = cluster_for_entity('emp1', [a], []); \
  b = ScoredEvent('e2','THREAT-DEVICE',now+timedelta(minutes=1),75.0,0.8,['THREAT-DEVICE','emp1-device'],'A2'); \
  out = cluster_for_entity('THREAT-DEVICE', [b], [inc]); \
  print('same incident:', out is inc, '| evidence:', out.evidence_refs, '| risk:', out.risk, out.severity)"
  ```
- Expect: `same incident: True | evidence: ['e1', 'e2'] | risk: 75 Critical`. The device event shares the `THREAT-DEVICE` edge so it joins the same incident — across entity boundaries.
- [ ] confirmed

### 3.5 THE exit criterion — multi-stage Account Compromise folds into ONE incident
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
  from analytics.baseline import build_individual
  from analytics.ml import train, score, clear_models
  from analytics.context import build as ctx_build
  from analytics.risk import compute, fuse, impact as impact_fn
  from analytics.correlation import cluster_for_entity, score_event
  from analytics.rules.out_of_scope import evaluate as scope
  from analytics.rules.volume_spike import evaluate as vol
  from analytics.rules.novel_peer import evaluate as peer

  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=30, events_per_day=12, seed=42)) if n]
  by_entity = defaultdict(list)
  for w in accumulate_all(back).values():
      by_entity[w.entity_ref].append(finalize(w))
  clear_models()
  train('global','__global__', [v for vs in by_entity.values() for v in vs], force=True)

  planted = inject_scenario(org, random.Random(7), 'compromise_chain', now)
  norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n]
  buckets = {w.entity_ref: finalize(w) for w in accumulate_all(norm).values()}
  emp_id = norm[0].entity_id
  actor = next(e for e in org.employees if e.emp_id == emp_id)
  profile = build_individual(emp_id, by_entity[emp_id])
  ml_emp = score('global','__global__', buckets[emp_id])

  emp_ev, dev_ev = [], []
  for ev in norm:
      is_emp = ev.entity_id == emp_id
      result = None
      if ev.event_type == 'file_access': result = scope(ev.__dict__, actor.department, org.resource_owner)
      elif ev.event_type == 'upload':    result = peer(ev.__dict__, set(), {})
      elif ev.event_type == 'download':  result = vol(buckets[emp_id], profile)
      sev = result.severity if result else 0.0
      ml = ml_emp if is_emp else (score('global','__global__', buckets[ev.entity_id]) if ev.entity_id in buckets else 0.0)
      ctx = ctx_build(ev, profile if is_emp else None, actor if is_emp else None, org.resource_owner)
      risk = compute(fuse([sev], ml), impact_fn(ctx.target_sensitivity, ctx.role_factor, ctx.dept_factor),
                     ctx.baseline_confidence, rule_bonus=sev*0.1)
      se = score_event(ev, risk.risk_100, severity=sev, alert_id=f'ALERT-{ev.event_id}')
      print(f'{se.event_id[:8]} {ev.event_type:>12} sev={sev:.2f} ml={ml:.2f} -> risk={se.risk:.1f} [{se.band}]')
      (emp_ev if is_emp else dev_ev).append(se)

  incident = cluster_for_entity(emp_id, emp_ev, [])
  folded = cluster_for_entity(dev_ev[0].entity_ref, dev_ev, [incident])
  print(f'folded into ONE incident: {folded is incident} | risk={folded.risk} [{folded.severity}]')
  print(f'evidence refs ({len(folded.evidence_refs)}): {[r[:8] for r in folded.evidence_refs]}')
  print(f'entity chain: {sorted(folded.entity_chain)}')
  print(f'timeline span: {int((folded.updated_at - folded.created_at).total_seconds()//60)} min')
  "@
  ```
- Expect: 5 rows (login / usb / file_access / download / upload), the last three Critical; then
  `folded into ONE incident: True | risk=90 [Critical]`, `evidence refs (5): ...`,
  `entity chain: ['EMP0xx', 'LPT-0xx', 'STORAGE.EXTERNAL.CLOUD', 'THREAT-DEVICE', 'bulk', 'hr_share']` (six entities),
  `timeline span: 11 min` (inside one 30-min rolling window).
  The five planted events become ONE incident — not five.
- [ ] confirmed

### 3.6 Persistence — the incident round-trips through the real tables
- Run:
  ```powershell
  & $py -c "import subprocess, sys, time, json; from pathlib import Path; \
  ROOT = Path.cwd(); \
  subprocess.run(['docker','compose','up','-d','postgres'], cwd=ROOT); time.sleep(2); \
  subprocess.run([sys.executable,'-m','alembic','-c','db/alembic.ini','upgrade','head'], cwd=ROOT, check=True); \
  from db.conn import connect; from db.dao import insert_alert, insert_incident, get_incidents; \
  from analytics.correlation import Incident; \
  conn = connect('postgresql://ueba:ueba_secret@localhost:5432/ueba'); \
  aid = insert_alert(conn, 'u1', 'High', 60, ['e1']); \
  iid = insert_incident(conn, Incident(entity_ref='u1', severity='Critical', risk=90, \
      entity_chain=['u1','THREAT-DEVICE'], evidence_refs=['e1','e2'], related_alert_ids=[str(aid)]).row()); \
  rows = get_incidents(conn, status='open'); hit = [r for r in rows if r['id']==iid][0]; \
  print('incident round-trip:', hit['risk'], hit['severity'], hit['entity_chain'], len(hit['evidence_refs'])); \
  conn.close()"
  ```
- Expect: `incident round-trip: 90 Critical ['THREAT-DEVICE', 'u1'] 2`.
- [ ] confirmed

### 3.7 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `334 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `63 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.7 confirmed by builder AND user
- [ ] `resolve_chain` extracts [actor, source, target, peer] non-empty, deduped
- [ ] An incident is created only when the window spans >= 2 distinct chain entities OR holds a single Critical event
- [ ] Consecutive events sharing a chain edge fold into ONE incident — even across entity boundaries (user → device)
- [ ] The multi-stage Account Compromise sequence collapses into ONE Critical incident (5 alerts, 5 evidence refs, ~11-min timeline)
- [ ] Replaying evidence never duplicates refs; risk is the max of the evidence
- [ ] Incidents round-trip through the real `alerts` / `incidents` tables
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase4e_diags.txt` — scored compromise chain + the single folded incident (risk 90 Critical, 6-entity chain, 5 evidence refs, 11-min timeline)
- [ ] `docs/verify_phase4e_pytest_unit.txt` — pytest summary `334 passed`
- [ ] `docs/verify_phase4e_pytest_int.txt` — pytest summary `63 passed`