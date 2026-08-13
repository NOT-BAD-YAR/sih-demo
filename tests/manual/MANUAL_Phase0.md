# MANUAL VERIFICATION — Phase 0 (Infrastructure Scaffolding)

> Run by BOTH the builder and the user independently. Phase 0 is PASS only
> when every checkbox below is completed and both parties' results match.

## 1. Prerequisites

- [ ] This is the builder's machine (auto tests passed: `45 passed`)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker Desktop running (`docker info` succeeds)
- [ ] Project venv exists and has deps: `.venv\Scripts\python.exe` (pytest, alembic, SQLAlchemy, pyyaml)

## 2. Setup steps

```powershell
# from project root U:\Projects\Insider-threat, using the project venv interpreter
.\scripts\dev.ps1 env         # creates .env if not present (no overwrite)
.\scripts\dev.ps1 up          # docker compose up -d
Set-Variable py .\.venv\Scripts\python.exe   # use the venv interpreter everywhere below
```

All `python`/`alembic` invocations below use `$py` (the project venv).

## 3. Step-by-step checks

### 3.1 Repo structure present
- Run: `Get-ChildItem -Name`
- Look for: `agents`, `simulator`, `streaming`, `analytics`, `api`, `dashboard`, `db`, `tests`, `docs`, `scripts`.
- Proof: all 10 folders listed above (plus root files `docker-compose.yml`, `.env.example`, `Makefile`, `pytest.ini`, `BUILD_METHODOLOGY.md`).
- [ ] confirmed

### 3.2 Analytics config imports
- Run: `& $py -c "from analytics import Config; c = Config.from_env(); print(c.kafka_bootstrap, c.risk_band_critical)"`
- Expect: `localhost:9092 75`
- [ ] confirmed

### 3.3 Infra boots healthy
- Run: `docker compose ps`
- Look for both `ueba-postgres` and `ueba-kafka` with status `(healthy)`.
- [ ] confirmed

### 3.4 Postgres accepting connections
- Run: `docker exec ueba-postgres pg_isready -U ueba -d ueba`
- Expect: `... accepting connections`
- [ ] confirmed

### 3.5 Kafka broker responding
- Run: `docker exec ueba-kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list`
- Expect: command returns (exit 0) listing internal topics (`__consumer_offsets`, etc.).
- [ ] confirmed

### 3.6 Host ports reachable
- Run: `Test-NetConnection localhost -Port 5432` and `Test-NetConnection localhost -Port 9092`
- Expect: `TcpTestSucceeded : True` for both.
- [ ] confirmed

### 3.7 Alembic wiring connects (no migrations yet)
- Run: `& $py -m alembic -c db/alembic.ini current 2>&1`
- Expect: prints `Context impl PostgresqlImpl` (INFO lines are normal; exit code must be `0`), **no** connection error.
- [ ] confirmed

### 3.8 Dev task runner help
- Run: `.\scripts\dev.ps1 help`
- Expect: task list printed (`up`, `down`, `logs`, `ps`, `test`, ...).
- [ ] confirmed

### 3.9 Full auto suite (reproducibility)
- Run: `& $py -m pytest -q`
- Expect: `45 passed` (structure+unit+integration all green).
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.9 all confirmed by builder AND user
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docker compose ps` output (healthy for both services) — save to `docs/verify_phase0_ps.txt`
- [ ] `pg_isready` + kafka `--list` outputs — save to `docs/verify_phase0_diags.txt`
- [ ] pytest summary line `45 passed` — save to `docs/verify_phase0_pytest.txt`