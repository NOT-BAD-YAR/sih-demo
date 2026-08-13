# MANUAL VERIFICATION — Phase 3 (PostgreSQL Storage)

> Run by BOTH the builder and the user independently. Phase 3 is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (`172 passed` — 98 Phase 0+1 + 46 Phase 2 + 28 Phase 3)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Postgres + Kafka run in containers)
- [ ] Python venv at `.venv` with `psycopg2` + `alembic` installed
- [ ] `.env` present (or export `POSTGRES_DSN=postgresql://ueba:ueba_secret@localhost:5432/ueba`)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d
# wait until `docker compose ps` shows BOTH postgres and kafka healthy (~60-90 s)
```

## 3. Step-by-step checks

### 3.1 Migrations apply cleanly to head
- Run: `& $py -m alembic -c db/alembic.ini upgrade head`
- Expect: no error output; migration `0001` applies (transactional DDL info lines only).
- [ ] confirmed

### 3.2 All 11 tables exist per the Phase 3 schema
- Run:
  ```powershell
  & $py -c "from db.conn import connect; from db.dao import schema_tables, SCHEMA_TABLES; \
  c = connect(); present = schema_tables(c); print('tables:', len(present)); \
  print('missing:', sorted(set(SCHEMA_TABLES) - present)); \
  print('all_present:', set(SCHEMA_TABLES) <= present); c.close()"
  ```
- Expect: `tables: <11+>`, `missing: []`, `all_present: True`.
- [ ] confirmed

### 3.3 Seed org loads (100 users / 80 entities / 7 peer groups) + 2 demo accounts
- Run: `& $py -m db.seed`
- Expect: `seeded org: {'peer_groups': 7, 'users': 100, 'entities': 80}` and `seeded demo accounts: 2`.
- [ ] confirmed

### 3.4 Seeding is idempotent
- Run: `& $py -m db.seed` again (second run)
- Expect: `users: 0`, `entities: 0` and `seeded demo accounts: 0` — re-running inserts nothing new.
- [ ] confirmed

### 3.5 Seeded data resolves correctly (peer group links + device ownership)
- Run:
  ```powershell
  & $py -c "from db.conn import connect, dict_cursor; from simulator.org import generate_org; \
  from db.seed import seed_org; c = connect(); seed_org(c, generate_org(seed=42)); \
  cur = dict_cursor(c); \
  cur.execute('SELECT count(*) AS n FROM users u JOIN peer_groups g ON g.id = u.peer_group_id'); \
  print('users_with_peer_group:', cur.fetchone()['n']); \
  cur.execute(\"SELECT count(*) AS n FROM entities WHERE kind='device' AND owner_user_id IS NOT NULL\"); \
  print('devices_with_owner:', cur.fetchone()['n']); c.close()"
  ```
- Expect: `users_with_peer_group: 100` and `devices_with_owner: 50`.
- [ ] confirmed

### 3.6 Insert → read roundtrip via DAO (event persisted then fetched back)
- Run:
  ```powershell
  & $py -c "from db.conn import connect; from db.dao import insert_event, get_event; \
  from datetime import datetime, timezone; from simulator.schema import build_event; \
  from streaming.producer import normalize_payload; \
  ev = build_event(entity_type='user', entity_id='EMP001', user_id='EMP001', event_type='login', \
  actor='EMP001', source_entity='LPT-001', target_entity='LPT-001', \
  ts=datetime.now(timezone.utc), ip='10.0.0.1', geo={'city':'Chennai','lat':13.08,'lon':80.27}); \
  p = normalize_payload(ev); c = connect(); \
  print('inserted:', insert_event(c, p)); \
  got = get_event(c, p['event_id']); \
  print('read_back:', got['event_type'], got['actor'], got['geo']['city'], got['bytes']); c.close()"
  ```
- Expect: `inserted: True` and `read_back: login EMP001 Chennai 0` — geo round-trips as JSONB.
- [ ] confirmed

### 3.7 Dedupe: `ON CONFLICT (event_id) DO NOTHING` rejects a redelivered copy
- Run:
  ```powershell
  & $py -c "from db.conn import connect; from db.dao import insert_event, count_events, get_event; \
  from datetime import datetime, timezone; from simulator.schema import build_event; \
  from streaming.producer import normalize_payload; \
  ev = build_event(entity_type='user', entity_id='EMP009', user_id='EMP009', event_type='mfa', \
  actor='EMP009', source_entity='LPT-009', target_entity='LPT-009', \
  ts=datetime.now(timezone.utc)); p = normalize_payload(ev); c = connect(); \
  print('first insert:', insert_event(c, p)); \
  print('redelivered insert:', insert_event(c, dict(p))); \
  print('total events now:', count_events(c)); c.close()"
  ```
- Expect: `first insert: True`, `redelivered insert: False`, `total events now: 1` — the duplicate event_id is absorbed by the DB unique key.
- [ ] confirmed

### 3.8 DB-level constraint rejects an invalid event_type
- Run:
  ```powershell
  & $py -c "from db.conn import connect; c = connect(); \
  try: \
      cur = c.cursor(); cur.execute(\"INSERT INTO raw_events (event_id, ts, ingested_at, event_type, outcome, sensitivity) VALUES ('bad-1', now(), now(), 'teleport', 'success', 'internal')\"); c.commit(); print('NO ERROR (BUG)') \
  except Exception as e: \
      print('rejected by CHECK:', type(e).__name__) \
  finally: \
      c.rollback(); c.close()"
  ```
- Expect: `rejected by CHECK: <Error>` — the schema guard holds, not silently accepted.
- [ ] confirmed

### 3.9 Graph-correlation indexes are present (actor / peer_entity / user ts)
- Run:
  ```powershell
  & $py -c "from db.conn import connect; c = connect(); cur = c.cursor(); \
  cur.execute(\"SELECT indexname FROM pg_indexes WHERE tablename='raw_events' ORDER BY indexname\"); \
  print('raw_events indexes:', [r[0] for r in cur.fetchall()]); c.close()"
  ```
- Expect: contains `ix_raw_events_actor`, `ix_raw_events_peer_ts`, `ix_raw_events_user_ts`, `ix_raw_events_ts`.
- [ ] confirmed

### 3.10 Full pipeline: Kafka producer → consumer → `raw_events` with duplicates absorbed
- Run:
  ```powershell
  & $py -m streaming persist --bootstrap localhost:9092 --group manual-p3-<yourname>
  ```
- Expect: `persist outcomes : [True, False]`, `raw_events before < N>`, `raw_events after < N+1>`, `net_new_rows : 1`, `persisted event_type : login`; the redelivered copy is rejected by the DB while exactly one row lands in `raw_events`.
- [ ] confirmed

### 3.11 SQL proof: seeded org + sample event visible via SQL text
- Run:
  ```powershell
  docker compose exec -T postgres psql -U ueba -d ueba -c "SELECT count(*) AS users FROM users; SELECT count(*) AS entities FROM entities; SELECT count(*) AS events FROM raw_events;"
  ```
- Expect: `users = 100`, `entities = 80`, `events >= 1` (the roundtrip event); plus no errors.
- [ ] confirmed

### 3.12 Full auto suite reproducible
- Run: `& $py -m pytest -q`
- Expect: `172 passed`
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.12 confirmed by builder AND user
- [ ] Migrations apply cleanly to head and are idempotent-safe
- [ ] Seeded org present (7/100/80) and drives the simulator as expected
- [ ] Event persistence verified: insert→read roundtrip, dedupe ON CONFLICT, DB CHECK constraints
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase3_migrations.txt` — `alembic upgrade head` output (3.1)
- [ ] `docs/verify_phase3_diags.txt` — outputs of 3.2–3.9 (tables, seed, roundtrip, dedupe, index)
- [ ] `docs/verify_phase3_pipeline.txt` — output of 3.10 producer→consumer→DB proof
- [ ] `docs/verify_phase3_psql.txt` — output of 3.11 SQL proof and `docker compose ps` health
- [ ] `docs/verify_phase3_pytest.txt` — pytest summary `172 passed`