# MANUAL VERIFICATION — Phase 1 (Event Schema + Simulator)

> Run by BOTH the builder and the user independently. Phase 1 is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (`98 passed` — 45 Phase 0 + 53 Phase 1)
- [ ] User has a separate verification session to reproduce these steps
- [ ] No external services required (simulator is pure Python; Docker NOT needed for Phase 1)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
```

## 3. Step-by-step checks

### 3.1 Schema is importable and enums are correct
- Run: `& $py -c "from simulator.schema import ENTITY_TYPES, EVENT_TYPES, OUTCOMES, SENSITIVITIES; print(ENTITY_TYPES); print(len(EVENT_TYPES)); print(OUTCOMES); print(SENSITIVITIES)"`
- Expect: `('user', 'device', 'server', 'app')`, `11` event types, outcomes `success/failure`, sensitivities `public/internal/confidential/restricted`.
- [ ] confirmed

### 3.2 A valid event validates cleanly
- Run: `& $py -c "from datetime import datetime, timezone; from simulator.schema import build_event, is_valid; e = build_event(entity_type='user', entity_id='EMP001', user_id='EMP001', event_type='login', actor='EMP001', source_entity='LPT-001', target_entity='LPT-001', ts=datetime.now(timezone.utc), ip='10.0.0.1', geo={'city':'Chennai','lat':13.08,'lon':80.27}); print(is_valid(e))"`
- Expect: `True`
- [ ] confirmed

### 3.3 An invalid event is rejected
- Run: `& $py -c "from datetime import datetime, timezone; from simulator.schema import build_event, is_valid; e = build_event(entity_type='rocket', entity_id='', event_type='login', actor='', ts=datetime.now(timezone.utc)); print(is_valid(e))"`
- Expect: `False`
- [ ] confirmed

### 3.4 Organisation generator is deterministic and shaped correctly
- Run: `& $py -c "from simulator.org import generate_org; o = generate_org(seed=42); print(len(o.employees), len(o.devices), len(o.servers), len(o.apps)); print(sorted({e.department for e in o.employees}))"`
- Expect: `100 50 20 10` and `['DevOps', 'Developers', 'Finance', 'HR', 'Security']`.
- [ ] confirmed

### 3.5 Same seed reproduces the same org
- Run: `& $py -c "from simulator.org import generate_org; a=generate_org(7); b=generate_org(7); print([e.emp_id for e in a.employees]==[e.emp_id for e in b.employees])"`
- Expect: `True`
- [ ] confirmed

### 3.6 90-day backfill runs and writes JSONL
- Run: `& $py -m simulator backfill --days 90 --jsonl docs/verify_phase1_backfill.jsonl`
- Run: `(Get-Content docs\verify_phase1_backfill.jsonl).Count`
- Expect: tens of thousands of lines (each = one valid event line); builder observed `75166`; user's count should be a comparable large number (> 50000).
- [ ] confirmed

### 3.7 JSONL uses the canonical `bytes` field and unique event_ids
- Run:
  ```powershell
  $lines = Get-Content docs\verify_phase1_backfill.jsonl
  $ev = $lines[0] | ConvertFrom-Json
  "has bytes: $($ev.PSObject.Properties.Name -contains 'bytes')"
  "dupes: $(($lines | % { ($_ | ConvertFrom-Json).event_id } | Group-Object | ? Count -gt 1).Count)"
  ```
- Expect: `has bytes: True` and `dupes: 0`
- [ ] confirmed

### 3.8 All five anomalies + compromise chain are produced with ground truth
- Run: `& $py -c "import random; from datetime import datetime, timezone; from simulator.org import generate_org; from simulator.anomaly import inject_scenario; from simulator.ground_truth import all_records, clear; org=generate_org(21); clear(); [inject_scenario(org, random.Random(1), s, datetime.now(timezone.utc)) for s in ['volume_spike','impossible_travel','out_of_scope','dormant','novel_peer','compromise_chain']]; [print(t.scenario, t.entity_id, t.expected_risk_band) for t in all_records()]"`
- Expect: exactly six lines, one per scenario, each with an entity and an expected risk band (High/Medium/Critical), including `compromise_chain → Critical`.
- [ ] confirmed

### 3.9 Scenario events are schema-valid
- Run: `& $py -c "import random; from datetime import datetime, timezone; from simulator.org import generate_org; from simulator.anomaly import inject_scenario; from simulator.schema import is_valid; from simulator.ground_truth import clear; org=generate_org(21); clear(); evs=inject_scenario(org,random.Random(1),'compromise_chain',datetime.now(timezone.utc)); print(len(evs), all(is_valid(e) for e in evs))"`
- Expect: `5 True`
- [ ] confirmed

### 3.10 Live scheduler produces valid events
- Run: `& $py -c "from simulator.org import generate_org; from simulator.live import run_live; from simulator.schema import is_valid; org=generate_org(11); e=run_live(org, max_ticks=1, seed=1); print(type(e).__name__, all(is_valid(x) for x in e))"`
- Expect: `list True` (a list, possibly empty if the current hour has no active employees — rerun accepts `True` on non-empty list; empty is OK too, list type printed)
- [ ] confirmed

### 3.11 Full auto suite reproducible
- Run: `& $py -m pytest -q`
- Expect: `98 passed`
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.11 confirmed by builder AND user
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase1_backfill.jsonl` — 90-day backfill output
- [ ] Console outputs for 3.4, 3.5, 3.8, 3.9 — save to `docs/verify_phase1_diags.txt`
- [ ] pytest summary `98 passed` — save to `docs/verify_phase1_pytest.txt`