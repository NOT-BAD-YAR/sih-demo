# MANUAL VERIFICATION — Phase 4D (Context + Risk Engine)

> Run by BOTH the builder and the user independently. Phase 4D is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (308 unit/structure/contract + 59 integration — 367 total)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Postgres/Kafka run in containers for the DB-backed check)
- [ ] Python venv at `.venv` with `scikit-learn` + `numpy` installed
- [ ] `.env` present (or `POSTGRES_DSN` exported)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d
# wait until `docker compose ps` shows BOTH postgres and kafka healthy (~60-90 s)
```

## 3. Step-by-step checks

### 3.1 Context + Risk modules present, API surface
- Run: `& $py -c "from analytics.context import build, ContextVector; from analytics.risk import compute, fuse, impact, band_of; print('context + risk apis ok')"`
- Expect: `context + risk apis ok`.
- [ ] confirmed

### 3.2 Factor tables (sensitivity / role / confidence)
- Run:
  ```powershell
  & $py -c "from analytics.context import sensitivity_score, role_factor, confidence_weight; \
  print([sensitivity_score(t) for t in ('public','internal','confidential','restricted')]); \
  print('HR Manager ->', role_factor('HR Manager'), '| Software Engineer ->', role_factor('Software Engineer')); \
  print('System Administrator ->', role_factor('System Administrator')); \
  print([confidence_weight(g) for g in ('LOW','MED','HIGH')])"
  ```
- Expect: `[0.1, 0.3, 0.6, 0.9]`; `HR Manager -> 0.9`, `Software Engineer -> 0.6`, `System Administrator -> 1.0`; `[0.4, 0.7, 1.0]`.
- [ ] confirmed

### 3.3 The risk formula is multiplicative (Risk = Anomaly × Impact × Confidence)
- Run:
  ```powershell
  & $py -c "from analytics.risk import compute; \
  r = compute(anomaly=0.5, impact=0.8, confidence=0.7); \
  print('risk:', r.risk_100, '| band:', r.band); \
  print('zero impact ->', compute(1.0, 0.0, 1.0).risk_100); \
  print('rule_bonus capped ->', compute(0.0, 0.0, 0.0, rule_bonus=1.0).risk_100)"
  ```
- Expect: `risk: 28.0 | band: Medium` (0.5×0.8×0.7×100), `zero impact -> 0.0`, `rule_bonus capped -> 15.0`.
- [ ] confirmed

### 3.4 Band boundaries
- Run: `& $py -c "from analytics.risk import band_of; print([(s, band_of(s)) for s in (0, 24.9, 25, 49.9, 50, 74.9, 75, 100)])"`
- Expect: `(0, Low), (24.9, Low), (25, Medium), (49.9, Medium), (50, High), (74.9, High), (75, Critical), (100, Critical)`.
- [ ] confirmed

### 3.5 Cold-start gentleness — sparse entity judged gently
- Run:
  ```powershell
  & $py -c "from analytics.risk import compute; \
  rich   = compute(anomaly=0.894, impact=0.6, confidence=1.0, rule_bonus=0.1); \
  sparse = compute(anomaly=0.894, impact=0.6, confidence=0.4, rule_bonus=0.1); \
  print('rich  :', rich.risk_100, rich.band); print('sparse:', sparse.risk_100, sparse.band); \
  print('sparse judged gently:', sparse.risk_100 < rich.risk_100)"
  ```
- Expect: `rich  : 63.6 High` and `sparse: 25.6 Medium` (approx) and `sparse judged gently: True`.
- [ ] confirmed

### 3.6 Real engine — all 5 planted anomalies produce defensible risk (THE exit criterion)
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
  from analytics.rules.volume_spike import evaluate as vol
  from analytics.rules.impossible_travel import evaluate as travel
  from analytics.rules.out_of_scope import evaluate as scope
  from analytics.rules.dormant import evaluate as dorm
  from analytics.rules.novel_peer import evaluate as peer

  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=30, events_per_day=12, seed=42)) if n]
  by_entity = defaultdict(list)
  for w in accumulate_all(back).values():
      by_entity[w.entity_ref].append(finalize(w))
  all_windows = [v for vecs in by_entity.values() for v in vecs]
  clear_models()
  train('global','__global__', all_windows, force=True)

  def find(eid):
      for e in org.employees:
          if e.emp_id == eid: return e
      for s in org.servers:
          if s.server_id == eid: return s
      return None

  for name in ('volume_spike','impossible_travel','out_of_scope','dormant','novel_peer'):
      planted = inject_scenario(org, random.Random(7), name, now)
      norm = [n for n in (validate(normalize_payload(e)) for e in planted) if n]
      ev = norm[0]
      actor = find(ev.entity_id)
      profile = build_individual(ev.entity_id, by_entity[ev.entity_id]) if ev.entity_id in by_entity else None
      win = finalize(next(iter(accumulate_all(norm).values())))
      ml = score('global','__global__', win)
      if name == 'volume_spike':  r = vol(win, profile)
      elif name == 'impossible_travel':
          pairs = sorted(((e.geo, e.ts) for e in norm if e.event_type=='login'), key=lambda p:p[1]); r = travel(pairs)
      elif name == 'out_of_scope': r = scope(ev.__dict__, actor.department, org.resource_owner)
      elif name == 'dormant':      r = dorm(ev.__dict__, {'start_hour':8,'end_hour':18}, staleness_days=45)
      else:
          srv = next(s for s in org.servers if s.server_id == ev.entity_id); r = peer(ev.__dict__, srv.peers, {})
      ctx = ctx_build(ev, profile, actor, org.resource_owner)
      risk = compute(fuse([r.severity], ml), impact_fn(ctx.target_sensitivity, ctx.role_factor, ctx.dept_factor),
                     ctx.baseline_confidence, rule_bonus=r.severity*0.1)
      print(f'{name:>16} severity={r.severity:.2f} confidence={ctx.baseline_confidence} -> risk={risk.risk_100:.1f} [{risk.band}]')
  "@
  ```
- Expect: 5 rows, each `0-100` with a valid band. The builder's reference (seed 42): volume_spike ~90 [Critical], impossible_travel ~76 [Critical], out_of_scope ~68 [High], dormant ~26 [Medium], novel_peer ~25 [Medium]. Cold-start anomalies (dormant/novel_peer) are deliberately NOT harsh because their actors have LOW confidence — that is the designed behaviour, not a miss.
- [ ] confirmed

### 3.7 Reproducibility — same inputs, same risk
- Run:
  ```powershell
  & $py -c "from analytics.risk import compute; \
  kw = dict(anomaly=0.65, impact=0.7, confidence=0.7, rule_bonus=0.05, components={'rules':[0.8],'ml':0.5}); \
  print('identical breakdown:', compute(**kw).breakdown == compute(**kw).breakdown)"
  ```
- Expect: `identical breakdown: True`.
- [ ] confirmed

### 3.8 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `308 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `59 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.8 confirmed by builder AND user
- [ ] `Risk = Anomaly × Impact × Confidence` is multiplicative, never additive-capped
- [ ] Risk is bounded 0-100 and maps to Low/Medium/High/Critical
- [ ] Anomaly fuses rules (0.7) with the ML signal (0.3); rule bonus is capped at 0.15
- [ ] Impact blends target sensitivity, role privilege, and department scope (out-of-scope = 1.4)
- [ ] Sparse/new entities are judged gently (LOW confidence = 0.4), never harshly
- [ ] All 5 planted anomalies yield defensible, reproducible risk through the real engine
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase4d_diags.txt` — composed risk table for the 5 planted anomalies + cold-start gentleness
- [ ] `docs/verify_phase4d_pytest_unit.txt` — pytest summary `308 passed`
- [ ] `docs/verify_phase4d_pytest_int.txt` — pytest summary `59 passed`