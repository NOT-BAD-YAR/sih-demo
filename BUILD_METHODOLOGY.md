# Project Build Methodology (Mandatory Process — Applies to Every Phase)

> This document defines the ONLY acceptable way to build this project.
> Read it before starting any phase and before claiming any phase is "done".

---

## 1. The Core Rule (Gate-Based Development)

**A phase is NOT complete until BOTH of its verification gates pass:**

```
  ┌────────────────────────────────────────────────────────────┐
  │  STEP 0: STRUCTURE FIRST                                  │
  │  Create/verify the proper folder & file structure for     │
  │  the phase BEFORE writing any code.                       │
  └────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  STEP 1: BUILD                                             │
  │  Implement the phase's modules following lld.md.           │
  └────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  STEP 2: AUTO VERIFICATION (MANDATORY)                     │
  │  Automated test suite covering EVERY functionality         │
  │  delivered in THIS phase. No phase moves forward without   │
  │  all automated tests passing.                              │
  └────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  STEP 3: MANUAL VERIFICATION (MANDATORY)                   │
  │  Document & perform a step-by-step manual verification.    │
  │  Shows the feature works by human-visible proof.           │
  └────────────────────────────────────────────────────────────┘
                              │
                              ▼
  ┌────────────────────────────────────────────────────────────┐
  │  STEP 4: VERIFY LOG + GATE                                │
  │  Record auto results + manual results + screenshots/       │
  │  outputs in Phase verification log. ONLY THEN move on.     │
  └────────────────────────────────────────────────────────────┘
                              │
                              ▼
                       ┌──────────────┐
                       │ NEXT PHASE   │
                       └──────────────┘

       NEVER skip Step 2 or Step 3. NEVER start the next phase
       until both gates for the current phase have passed and
       been recorded.
```

---

## 2. Project Structure (Root Layout — Standard for All Phases)

```
insider-threat/
│
├── plan.md                      # High-level build plan (phases, decisions)
├── lld.md                       # Low-Level Design (module detail for every phase)
├── architecture.md              # System architecture diagram
├── BUILD_METHODOLOGY.md         # THIS file (the process rules)
├── README.md                    # How to run the whole project
│
├── agents/                      # Phase 8 — Windows endpoint agent
├── simulator/                   # Phase 1 — event generator (primary data source)
├── streaming/                   # Phase 2 — Kafka topics, producer, consumer
├── analytics/                   # Phase 4 — engine (processor, features, baseline,
│                                #   rules, ml, context, risk, correlation, runner)
├── api/                         # Phase 6 — FastAPI service
├── dashboard/                   # Phase 7 — React + TS SOC dashboard
├── db/                          # Phase 3 — Alembic migrations, seed, DAOs
├── tests/                       # ALL test suites (phase-scoped, see §3)
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── manual/                  # Manual runbooks (per phase: MANUAL_<phase>.md)
│   └── eval/                    # Phase 9 — metrics + ablation
├── docs/                        # Docs, notes, demo scripts
└── docker-compose.yml           # Kafka, Postgres, engine, API, dashboard
```

### Per-phase structure rules
- Every phase works inside its own folders ONLY — no cross-phase file edits.
- Shared/borrowed code (e.g. schema, DAOs) lives in its owning phase; later phases import it, never copy it.
- Each phase ships: `src/` (or named module folders), `tests/` scoped to the phase, and a `manual/` runbook.

---

## 3. Automated Test Requirements (Step 2)

### Coverage rule
The test suite for a phase MUST exercise **every functionality delivered in that phase** — not "some" of it. Map each module/feature to at least one test:

| Phase | Minimum required automated coverage (examples) |
|---|---|
| 0 | infra: containers healthy, config loads from env, migration runner connects |
| 1 | schema validation (valid/nvalid events), org generator determinism, backfill produces N days, each of the 5 anomalies + chain is generated with ground_truth rows |
| 2 | producer publishes, consumer receives, at-least-once redelivery handled, duplicate event_ids rejected, lag reported |
| 3 | every table exists per schema, seed org loads, insert→read roundtrip, dedupe ON CONFLICT works, DAO functions pass |
| 4 | feature accumulation, all 5 rule detectors (each rule: trigger + non-trigger + reason string), confidence thresholds, cold-start fallback, risk formula→bands table, correlation folds a chain into 1 incident |
| 5 | escalation (band→alert/incident), lifecycle transitions, playbook mapping, action auditing |
| 6 | every endpoint: auth (401/403), roles enforced, CRUD, evidence replay returns real events |
| 7 | build/lint/typecheck pass, route guards, key component renders (smoke tests) |
| 8 | normalizer maps each reader source, batching flushes, reader disable-on-error graceful |
| 9 | metrics computed from ground_truth, ablation A/B/C runs and reports, demo runbook executes |

### Commands
- Python: `pytest -m <phase>` (each phase's tests tagged, e.g. `@pytest.mark.phase1`).
- Dashboard: `npm run build`, `tsc --noEmit`, `npm run lint`, plus vitest smoke tests.
- Contract: `pytest -m contract`.

### Definition of Done (auto gate)
`All tests green, zero skipped, zero xfail-as-workaround.` A failed test must be fixed or explicitly waived with a written reason — never silently deleted.

---

## 4. Manual Verification (Step 3)

Every phase MUST ship a manual runbook: `tests/manual/MANUAL_<PhaseN>.md`.

### Required runbook format
```
# MANUAL VERIFICATION — Phase N
## 1. Prerequisites            (what must be running, env vars, services)
## 2. Setup steps              (commands to run, data to seed)
## 3. Step-by-step checks      (numbered; each: what to run, what to LOOK at,
##                              what proves it worked, expected output/photo)
## 4. Success criteria         (checkboxes — all must tick before moving on)
## 5. Evidence captured        (output files, screenshots, log excerpts)
```

### How WE do it
- I build the runbook, run through it, and report results + captured evidence.
- YOU (the user) independently run the same runbook on your machine and confirm.
- Any discrepancy between my result and yours → treat phase as NOT passed; fix and rerun.

### Notify + gate
After auto tests pass and manual runbook is confirmed, I present the **Phase Verification Log** (below). Only your approval moves us to the next phase.

---

## 5. Phase Verification Log (persistent record)

Maintained at `docs/verification_log.md`, table per phase:

```
| Phase | Auto tests passed (count) | Manual runbook confirmed | Evidence refs | Status | Date |
|-------|---------------------------|--------------------------|---------------|--------|------|
| 0     | 12 / 12                   | YES                      | verify_log/p0 | PASS   | ...  |
```

`Status` is the single source of truth: `PASS` only when both gates recorded.

---

## 6. Guardrails (Non-Negotiable)

1. **Structure before code** — Step 0 always runs first.
2. **No next phase until both gates pass** — no "let's just also build ahead".
3. **Auto tests must actually fail when code breaks** (tests that always pass prove nothing).
4. **Zero silent skips** in tests; anything skipped must be documented.
5. **Manual runbook is executed by BOTH builder and the user** before a phase is marked PASS.
6. **Every phase's manual runbook lives in `tests/manual/`** and shows human-visible proof (output/API response/logs/screenshots).
7. **Verification log updated at every phase end** — never run ahead of the log.

---

## 7. Where This Lives

- This file: `BUILD_METHODOLOGY.md` (project root) — the rules.
- Per-phase design: `lld.md`.
- Phase order/gates: `plan.md`.
- Verification history: `docs/verification_log.md`.

If any instruction conflicts with this file, **this file wins.**