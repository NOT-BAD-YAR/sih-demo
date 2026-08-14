# MANUAL VERIFICATION — Phase 8 (Windows Endpoint Agent)

> Run by BOTH the builder and the user independently. Phase 8 is PASS only
> when every checkbox is completed and both parties' results match.
> Run on a real Windows box (with or without Sysmon / Security-log access).

## 1. Prerequisites

- [ ] Builder's machine: full auto suite passed (`592 passed` incl. Phase 8 unit + 4 Kafka integration)
- [ ] Windows 10/11 box, Python 3.11+ with the repo's `.venv`
- [ ] Docker + Compose up (Postgres + Kafka) for the live-ship checks
- [ ] Agent package imports cleanly: `& .\.venv\Scripts\python.exe -X utf8 -c "import agents.windows_agent"`

## 2. Setup

No install needed — the agent uses only built-in Windows tools
(`wevtutil`, `powershell`, `tasklist`) and the repo's `streaming` producer.

```powershell
# make a small demo watch folder the file_watcher will pick up
$d = "C:\Users\<you>\agentwatch"; New-Item -ItemType Directory -Force -Path $d
Set-Content -Path "$d\report.xlsx" -Value "demo"
Set-Content -Path "$d\notes.pdf"   -Value "demo"
```

## 3. Step-by-step checks

### 3.1 Dry-run — collection + normalization on the real box
- Run: `& .\.venv\Scripts\python.exe -X utf8 -m agents.windows_agent --once --dry-run`
- LOOK at: lines `[agent] <ts> <event_type> <user> target=... topic=<topic>`.
- Proves: real events are collected from your machine and normalized into the
  Common Event Schema (process → `device-events`, file → `file-events`).
- Expected: your hostname as `entity_id`; event types match the schema.
- [ ] confirmed

### 3.2 Fail-open — unavailable sources don't stop the agent
- Same command as 3.1. LOOK at the reader status lines printed at exit.
- If your account can't read the Security log / Sysmon isn't installed, expect:
  `reader security_log: disabled ... unavailable at startup` and
  `reader sysmon: disabled ... unavailable at startup` — while
  `file_watcher` / `process` / `usb` stay `enabled`.
- Proves: readers degrade gracefully; the agent keeps running (fail-open).
- [ ] confirmed

### 3.3 Live ship — real events reach Kafka
- Start Kafka/Postgres: `docker compose up -d`; wait until `kafka` is `healthy`.
- Run: `$env:AGENT_READERS="file_watcher"; $env:AGENT_WATCH_DIRS="C:\Users\<you>\agentwatch";
  $env:AGENT_HOSTNAME="<HOST>"; & .\.venv\Scripts\python.exe -X utf8 -m agents.windows_agent --once`
- LOOK at: `stats: {'buffered': 0, 'sent': N, 'flushes': 1, 'failures': 0}`.
- Proves: normalized events buffered and flushed to Kafka with zero failures.
- [ ] confirmed

### 3.4 Broker check — the events are on the topics the engine reads
- Inspect topic watermarks (or any consumer). Expected nonzero counts on the
  topics matching your readers (e.g. `file-events` for file_watcher,
  `device-events` for process).
- Proves: the endpoint agent produces data the UEBA engine consumes — the same
  pipeline the simulator uses.
- [ ] confirmed

### 3.5 Config — reader selection via env / agent.toml
- Run the agent with `$env:AGENT_READERS="process"` only → status shows only
  `process` enabled; others are absent from the reader list.
- Optionally point `--config` at a JSON/toml file (`readers`, `watch_dirs`,
  `hostname`) and confirm it loads.
- Proves: the agent is configurable per endpoint.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.5 confirmed by builder AND user
- [ ] Agent collects REAL endpoint events (login/file/USB/process depending on privileges) and normalizes them into the Common Event Schema
- [ ] Unavailable readers (no Security-log permission, no Sysmon) disable gracefully and the rest keep running
- [ ] Batching flushes on count/time and ships to Kafka with zero duplicate event_ids (schema-valid, `event_id` unique)
- [ ] Events land on the correct topics that the engine consumes (`auth-events`, `file-events`, `device-events`, …)
- [ ] No unexplained failures; discrepancies documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase8_dryrun.txt` — dry-run output: normalized real events + disabled-reader lines
- [ ] `docs/verify_phase8_live.txt` — live run: `sent: 3, failures: 0` for the file_watcher demo
- [ ] `docs/verify_phase8_kafka.txt` — broker watermarks: `file-events` / `device-events` nonzero
- [ ] `docs/verify_phase8_pytest_full.txt` — pytest summary `592 passed`