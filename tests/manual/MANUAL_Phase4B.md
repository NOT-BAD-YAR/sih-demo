# MANUAL VERIFICATION — Phase 4B (Rule Detectors)

> Run by BOTH the builder and the user independently. Phase 4B is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (230 unit/structure/contract + 45 integration — 275 total)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Postgres/Kafka run in containers for DB checks)
- [ ] Python venv at `.venv` with `psycopg2`, `alembic`, `scikit-learn` installed
- [ ] `.env` present (or `POSTGRES_DSN` exported)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d
# wait until `docker compose ps` shows BOTH postgres and kafka healthy (~60-90 s)
```

## 3. Step-by-step checks

### 3.1 Rules package present + registry covers the 5 canonical cases
- Run: `& $py -c "from analytics.rules import rule_names, REGISTRY; print(sorted(rule_names())); print(len(REGISTRY))"`
- Expect: `['dormant', 'impossible_travel', 'novel_peer', 'out_of_scope', 'volume_spike']` and `5`.
- [ ] confirmed

### 3.2 Rule 1 — Volume spike
- Run:
  ```powershell
  & $py -c "from analytics.rules.volume_spike import evaluate; \
  p = {'feature_stats': {'volume': {'mean': 10*1024*1024, 'std': 0, 'count': 60, 'confidence': 'HIGH'}}}; \
  r = evaluate({'volume': 100*1024*1024, 'event_count': 5}, p); \
  print('triggered:', r.triggered); print('severity:', round(r.severity, 3)); print('explanation:', r.explanation); \
  r2 = evaluate({'volume': 10*1024*1024, 'event_count': 5}, p); \
  print('normal triggered:', r2.triggered)"
  ```
- Expect: `triggered: True`, `severity: > 0`, explanation mentions `individual baseline`; the normal window does NOT trigger (`normal triggered: False`).- [ ] confirmed

### 3.3 Rule 2 — Impossible travel
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone, timedelta; from analytics.rules.impossible_travel import evaluate; \
  t = datetime(2026,1,15,10,0,tzinfo=timezone.utc); \
  pairs = [({'city':'Chennai','lat':13.08,'lon':80.27}, t), ({'city':'Delhi','lat':28.61,'lon':77.21}, t+timedelta(minutes=20))]; \
  r = evaluate(pairs); print('triggered:', r.triggered); print('explanation:', r.explanation); \
  pairs2 = [({'city':'Chennai','lat':13.08,'lon':80.27}, t), ({'city':'Chennai','lat':13.08,'lon':80.27}, t+timedelta(hours=2))]; \
  print('same city triggered:', evaluate(pairs2).triggered)"
  ```
- Expect: `triggered: True` with an explanation citing Chennai → Delhi and `km/h`; the same-city pair does NOT trigger.
- [ ] confirmed

### 3.4 Rule 3 — Out-of-scope access
- Run:
  ```powershell
  & $py -c "from analytics.rules.out_of_scope import evaluate; \
  access = {'HRMS':'HR','Finance-DB':'Finance','git':'Developers'}; \
  ev = {'entity_id':'EMP001','target_entity':'Finance-DB','file_path':'/Finance/Finance-DB/a.xlsx'}; \
  r = evaluate(ev, 'HR', access); print('triggered:', r.triggered); print('explanation:', r.explanation); \
  r2 = evaluate({'entity_id':'EMP001','target_entity':'HRMS','file_path':'/HR/HRMS/a.xlsx'}, 'HR', access); \
  print('in-scope triggered:', r2.triggered)"
  ```
- Expect: `triggered: True`, explanation names Finance + HR; the in-scope HRMS access does NOT trigger.
- [ ] confirmed

### 3.5 Rule 4 — Dormant account activation
- Run:
  ```powershell
  & $py -c "from datetime import datetime, timezone; from analytics.rules.dormant import evaluate; \
  ev = {'entity_id':'EMP005','ts': datetime(2026,1,15,2,30,tzinfo=timezone.utc)}; \
  r = evaluate(ev, {'start_hour':8,'end_hour':18}, staleness_days=45); \
  print('triggered:', r.triggered); print('explanation:', r.explanation); \
  r2 = evaluate(ev, {'start_hour':8,'end_hour':18}, staleness_days=5); \
  print('recent triggered:', r2.triggered)"
  ```
- Expect: `triggered: True` citing `45 days` and `02:00`; an account idle only 5 days does NOT trigger.
- [ ] confirmed

### 3.6 Rule 5 — Novel peer
- Run:
  ```powershell
  & $py -c "from analytics.rules.novel_peer import evaluate; \
  known = {'SRV-01','LPT-001','APP-01'}; \
  r = evaluate({'entity_id':'SRV-02','peer_entity':'UNKNOWN-42'}, known, {}); \
  print('triggered:', r.triggered); print('severity:', r.severity); print('explanation:', r.explanation); \
  r2 = evaluate({'entity_id':'SRV-02','peer_entity':'LPT-001'}, known, {}); \
  print('known triggered:', r2.triggered)"
  ```
- Expect: `triggered: True`, `severity: 0.8`, explanation names `UNKNOWN-42`; the known peer does NOT trigger.
- [ ] confirmed

### 3.7 All 5 planted anomalies are detected by the real engine
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
  from analytics.rules.volume_spike import evaluate as vol
  from analytics.rules.impossible_travel import evaluate as travel
  from analytics.rules.out_of_scope import evaluate as scope
  from analytics.rules.dormant import evaluate as dorm
  from analytics.rules.novel_peer import evaluate as peer

  now = datetime(2026,1,20,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  by_entity = defaultdict(list)
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=30, events_per_day=12, seed=42)) if n]
  for w in accumulate_all(back).values():
      by_entity[w.entity_ref].append(finalize(w))
  for name in ('volume_spike','impossible_travel','out_of_scope','dormant','novel_peer'):
      planted = inject_scenario(org, random.Random(42), name, now)
      norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n]
      if name == 'volume_spike':
          emp = planted[0].entity_id
          prof = build_individual(emp, by_entity[emp])
          w = next(iter(accumulate_all(norm).values()))
          r = vol(finalize(w), prof)
      elif name == 'impossible_travel':
          pairs = sorted(((e.geo, e.ts) for e in norm if e.event_type=='login'), key=lambda p:p[1])
          r = travel(pairs)
      elif name == 'out_of_scope':
          e = norm[0]
          emp = next(x for x in org.employees if x.emp_id==e.entity_id)
          r = scope(e.__dict__, emp.department, org.resource_owner)
      elif name == 'dormant':
          e = norm[0]
          r = dorm(e.__dict__, {'start_hour':8,'end_hour':18}, staleness_days=45)
      else:
          e = norm[0]
          srv = next(s for s in org.servers if s.server_id==e.entity_id)
          r = peer(e.__dict__, srv.peers, {})
      print(name, '->', 'DETECTED' if r.triggered else 'MISSED', '|', r.explanation[:80])
  "@
  ```
- Expect: every scenario prints `-> DETECTED` with a human-readable explanation. This is the Phase 4B exit criterion.
- [ ] confirmed

### 3.8 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `230 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `45 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.8 confirmed by builder AND user
- [ ] Registry exposes exactly the 5 canonical rule detectors
- [ ] Normal (in-baseline) behaviour never triggers; anomalous behaviour always triggers
- [ ] Every explanation is plain-language ("why flagged") and cites concrete numbers
- [ ] Each planted anomaly is detected when run through the real engine
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase4b_diags.txt` — outputs of 3.2–3.7
- [ ] `docs/verify_phase4b_pytest_unit.txt` — pytest summary `230 passed`
- [ ] `docs/verify_phase4b_pytest_int.txt` — pytest summary `45 passed`
