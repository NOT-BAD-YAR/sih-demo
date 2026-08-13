# MANUAL VERIFICATION — Phase 2 (Kafka Streaming Pipeline)

> Run by BOTH the builder and the user independently. Phase 2 is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (`144 passed` — 98 Phase 0+1 + 46 Phase 2)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Kafka runs in a container)
- [ ] Python venv at `.venv` with `confluent-kafka` installed

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d kafka
# wait until `docker compose ps` shows kafka healthy (about 30-60 s)
```

## 3. Step-by-step checks

### 3.1 Topic map + partition keys load correctly (no broker needed)
- Run: `& $py -c "from streaming.topics import TOPICS, topic_for, partition_key, TOPIC_KEY_FIELD; print(TOPICS); print('login->', topic_for('login')); print('network_conn->', topic_for('network_conn')); print('privilege->', topic_for('privilege')); print('key=', partition_key({'event_type':'download','entity_id':'EMP100'}))"`
- Expect: 5 topics with partitions `{auth-events:4, file-events:4, network-events:4, device-events:4, privilege-events:2}`; `login->auth-events`; `network_conn->network-events`; `privilege->privilege-events`; `key=b'EMP100'`.
- [ ] confirmed

### 3.2 A valid event becomes a schema-valid, JSON-shippable payload
- Run: `& $py -c "from datetime import datetime,timezone; from simulator.schema import build_event,is_valid; from streaming.producer import normalize_payload; from simulator.schema import from_dict; e=build_event(entity_type='user',entity_id='EMP001',user_id='EMP001',event_type='login',actor='EMP001',source_entity='LPT-001',target_entity='LPT-001',ts=datetime.now(timezone.utc)); p=normalize_payload(e); print(is_valid(e), p['event_type'], 'bytes' in p, 'ingested_at' in p); print(is_valid(from_dict(p)))"`
- Expect: `True login True True` and `True` (round-trips back to a valid Event).
- [ ] confirmed

### 3.3 Kafka is reachable and all 5 topics are provisioned with the right partitions
- Run: `& $py -m streaming ensure-topics --bootstrap localhost:9092`
- Run: `& $py -m streaming demo --bootstrap localhost:9092 --events 8 --group manual-phase2-<yourname>`
  - Watch for the **health** line: `{{'kafka': True, 'topics': {{'auth-events': {{'exists': True, 'partitions': 4}}, ... 'privilege-events': ... 2}}}}`
- Expect: `topics: {...'exists'...}` for all 5; demo prints `consumer joined group: True`, then `applied unique events : 8 / 8`, `rejected duplicates : 1`, and a lag dict.
- [ ] confirmed

### 3.4 Produce → consume round-trip on a real broker
- Run: `& $py -m streaming roundtrip --group manual-rt-<yourname>`
- Expect: `produced event_id : <uuid>`, `consumed : True`, `event_type matches: True`.
- [ ] confirmed

### 3.5 At-least-once redelivery + duplicate rejection
- Run: `& $py -m streaming dedupe --group manual-dp-<yourname>`
- Expect: `delivered twice? : True`, `applied unique : 1`, `duplicates rejected: 1` — the redelivered copy of the same `event_id` is dropped.
- [ ] confirmed

### 3.6 Per-entity ordering preserved by partition key
- Run: `& $py -c "from streaming.topics import partition_key; a=partition_key({'event_type':'login','user_id':'EMP100'}); b=partition_key({'event_type':'mfa','user_id':'EMP100'}); print(a==b, a)"`
- Expect: `True b'EMP100'` — same entity always lands in the same partition, so ordering holds.
- [ ] confirmed

### 3.7 Lag is reported (backlog is visible)
- Run: `& $py -m streaming demo --bootstrap localhost:9092 --events 4 --group manual-lag-empty` — this consumes everything (lag 0), then:
- Run: `& $py -c "from datetime import datetime,timezone; from simulator.schema import build_event; from streaming.producer import EventProducer,normalize_payload; b='localhost:9092'; pro=EventProducer(b,'network-events');
  for i in range(3):
    ev=build_event(entity_type='server',entity_id='SRV99',user_id='',event_type='network_conn',actor='SRV99',source_entity='SRV99',target_entity='srv01',peer_entity=f'10.9.9.{i+1}',ts=datetime.now(timezone.utc)); pro.send(normalize_payload(ev))
  pro.flush(timeout=10); print('produced 3 network events')"`
- Run: `& $py -m streaming lag --group manual-never-consumed-<yourname>`
- Expect: the lag dict shows `network-events:<n>: <>= 0` for the partitions holding those events (at least one partition `> 0`), proving unresumed backlog is visible.
- [ ] confirmed

### 3.8 Full auto suite reproducible
- Run: `& $py -m pytest -q`
- Expect: `144 passed`
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.8 confirmed by builder AND user
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase2_diags.txt` — outputs of 3.1, 3.3, 3.7 (health, topics, demo, lag)
- [ ] `docs/verify_phase2_console.txt` — outputs of 3.4, 3.5, 3.6 console commands
- [ ] `docs/verify_phase2_pytest.txt` — pytest summary `144 passed`