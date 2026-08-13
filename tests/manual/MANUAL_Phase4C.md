# MANUAL VERIFICATION — Phase 4C (ML Anomaly Detection — Isolation Forest)

> Run by BOTH the builder and the user independently. Phase 4C is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (265 unit/structure/contract + 52 integration — 317 total)
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

### 3.1 ML module present + API surface
- Run: `& $py -c "from analytics.ml import train, score, featurize, retrain_schedule, clear_models, ML_FEATURES; print('ML_FEATURES:', ML_FEATURES); print('apis ok')"`
- Expect: the 7 feature names printed and `apis ok`.
- [ ] confirmed

### 3.2 Featurize builds the numeric matrix
- Run:
  ```powershell
  & $py -c "from analytics.ml import featurize, ML_FEATURES; \
  X = featurize([{'volume': 100}]); \
  print('shape:', X.shape); print('missing event_count ->', X[0][ML_FEATURES.index('event_count')])"
  ```
- Expect: `shape: (1, 7)`; the missing `event_count` column is `0.0`.
- [ ] confirmed

### 3.3 Train gate — no model below ML_MIN_WINDOWS (default 20)
- Run:
  ```powershell
  & $py -c "from analytics.ml import train, MODEL_CACHE, clear_models; clear_models(); \
  w = lambda v: {'volume': v, 'event_count': 5, 'active_hours_frac': 0.5, 'location_count': 1, 'location_dist_km': 0.0, 'fail_rate': 0.0, 'staleness_days': 0}; \
  print('5 windows -> trained?', train('individual','EMP001',[w(100+i) for i in range(5)])); \
  print('cache empty?', MODEL_CACHE == {}); \
  print('20 windows -> trained?', train('individual','EMP002',[w(100+i) for i in range(20)])); \
  print('key present?', 'individual:EMP002' in MODEL_CACHE); \
  print('force 2 -> trained?', train('individual','EMP003',[w(100+i) for i in range(2)], force=True))"
  ```
- Expect: `5 windows -> trained? False`, `cache empty? True`, `20 windows -> trained? True`, `key present? True`, `force 2 -> trained? True`.
- [ ] confirmed

### 3.4 Score separates normal from extreme volume
- Run:
  ```powershell
  & $py -c "from analytics.ml import train, score, clear_models; clear_models(); \
  w = lambda v, i: {'volume': v+i, 'event_count': 4+(i%3), 'active_hours_frac': 0.4+0.1*(i%4), 'location_count': 1+(i%2), 'location_dist_km': 0.0, 'fail_rate': 0.0, 'staleness_days': 0}; \
  hist = [w(100.0, i) for i in range(60)]; train('individual','EMP001', hist, force=True); \
  normal = score('individual','EMP001', w(130.0, 3)); \
  spike  = score('individual','EMP001', dict(w(130.0, 3), volume=1_000_000)); \
  print(f'normal anomaly = {normal:.4f}'); print(f'spike anomaly  = {spike:.4f}'); \
  print('spike > normal:', spike > normal); print('bounded:', 0.0 <= spike <= 1.0)"
  ```
- Expect: `spike > normal: True` and `bounded: True` (the anomaly signal is a 0-1 supplement, never "malice").
- [ ] confirmed

### 3.5 Cold-start fallback — sparse entity uses the peer-group model
- Run:
  ```powershell
  & $py -c "from analytics.ml import train, score, clear_models; clear_models(); \
  w = lambda v, i: {'volume': v+i, 'event_count': 4+(i%3), 'active_hours_frac': 0.4+0.1*(i%4), 'location_count': 1+(i%2), 'location_dist_km': 0.0, 'fail_rate': 0.0, 'staleness_days': 0}; \
  train('peer_group','HR',[w(50.0, i) for i in range(40)], force=True); \
  print('sparse via peer_group:HR ->', score('individual','EMP001', w(500.0, 1), fallback_keys=['peer_group:HR','global:__global__'])); \
  print('no models at all  ->', score('individual','GHOST', w(500.0, 1)))"
  ```
- Expect: the first print is non-zero (fallback used); the second is exactly `0.0` (neutral, no signal).
- [ ] confirmed

### 3.6 Daily rolling retrain only touches trained models
- Run:
  ```powershell
  & $py -c "from analytics.ml import train, retrain_schedule, clear_models; clear_models(); \
  w = lambda v: {'volume': v, 'event_count': 5, 'active_hours_frac': 0.5, 'location_count': 1, 'location_dist_km': 0.0, 'fail_rate': 0.0, 'staleness_days': 0}; \
  train('global','__global__',[w(100+i) for i in range(40)], force=True); \
  print('retrained:', retrain_schedule({'global:__global__':[w(200+i) for i in range(30)], 'individual:NEVER':[w(i) for i in range(30)]}))"
  ```
- Expect: `retrained: ['global:__global__']` — `individual:NEVER` was never trained so it is skipped.
- [ ] confirmed

### 3.7 Real engine — planted volume-spike window scores above normal (THE exit criterion)
- Run:
  ```powershell
  & $py -c @"
  import statistics, random
  from datetime import datetime, timezone
  from collections import defaultdict
  from simulator.org import generate_org
  from simulator.engine import run_backfill
  from simulator.anomaly import inject_scenario
  from streaming.producer import normalize_payload
  from analytics.processor import validate
  from analytics.features import accumulate_all, finalize
  from analytics.ml import train, score, clear_models

  now = datetime(2026,2,1,10,30,tzinfo=timezone.utc)
  org = generate_org(seed=42)
  back = [n for n in (validate(normalize_payload(e)) for e in run_backfill(org, days=30, events_per_day=12, seed=42)) if n]
  by_entity = defaultdict(list)
  for w in accumulate_all(back).values():
      by_entity[w.entity_ref].append(finalize(w))
  all_windows = [v for vecs in by_entity.values() for v in vecs]
  clear_models()
  train('global','__global__', all_windows, force=True)
  normal_scores = [score('global','__global__', w) for w in all_windows]
  planted = inject_scenario(org, random.Random(1), 'volume_spike', now)
  sn = [n for n in (validate(normalize_payload(e)) for e in planted) if n]
  spike_w = finalize(next(iter(accumulate_all(sn).values())))
  spike = score('global','__global__', spike_w)
  print(f'normal anomaly  min/median/max = {min(normal_scores):.3f}/{statistics.median(normal_scores):.3f}/{max(normal_scores):.3f}')
  print(f'planted volume-spike anomaly  = {spike:.3f}  (volume {spike_w[chr(118)+chr(111)+chr(108)+chr(117)+chr(109)+chr(101)]})')
  print('spike flagged (0.5+):', spike > 0.5, '| above normal median:', spike > statistics.median(normal_scores))
  "@
  ```
- Expect: planted spike prints `spike flagged (0.5+): True` and `above normal median: True` while typical normal windows stay low (median well below 0.5).
- [ ] confirmed

### 3.8 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `265 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `52 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.8 confirmed by builder AND user
- [ ] Isolation Forest trains at individual / peer-group / global levels with a `count >= 20` gate (force for aggregated levels)
- [ ] Sparse entities fall back peer-group → global → neutral 0 (cold start) — never a false "0.5 everywhere"
- [ ] Score is bounded 0-1 and rises for anomalous windows (sign verified: normal stays LOW, spike goes HIGH)
- [ ] Rolling retrain only rebuilds models that already exist
- [ ] Planted volume-spike is flagged by the IF signal against the real engine
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase4c_diags.txt` — outputs of 3.2–3.7
- [ ] `docs/verify_phase4c_pytest_unit.txt` — pytest summary `265 passed`
- [ ] `docs/verify_phase4c_pytest_int.txt` — pytest summary `52 passed`
