# MANUAL VERIFICATION — Phase 6 (REST API · JWT · RBAC · Evidence Replay)

> Run by BOTH the builder and the user independently. Phase 6 is PASS only
> when every checkbox is completed and both parties' results match.
> ALL Python commands use the project venv interpreter.

## 1. Prerequisites

- [ ] Builder's machine: auto tests passed (423 unit/structure/contract + 89 integration — 512 total)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose available (Postgres + Kafka run in containers)
- [ ] Python venv at `.venv` with FastAPI stack installed: `fastapi`, `uvicorn`, `python-jose[cryptography]`, `httpx`, `pydantic`, plus `scikit-learn` + `numpy`
- [ ] `.env` present (or `POSTGRES_DSN` exported)

## 2. Setup steps

```powershell
Set-Variable py .\.venv\Scripts\python.exe
docker compose up -d postgres kafka
# wait until `docker compose ps` shows both healthy (~30-60 s)
```

## 3. Step-by-step checks

### 3.1 API surface + JWT auth
- Run:
  ```powershell
  & $py -c "from api.main import app; p = sorted(app.openapi()['paths']); print(len(p), 'paths'); print('auth/login', '/auth/login' in p, '| incidents evidence', '/incidents/{incident_id}/evidence' in p)"
  ```
- Expect: `17 paths`, `auth/login True | incidents evidence True`.
- [ ] confirmed

### 3.2 Login, token issuance, role claims, wrong-password rejection
- Run:
  ```powershell
  & $py -c "from api.auth import issue_token, decode_token; t = issue_token('alice','analyst'); p = decode_token(t); print('sub', p['sub'], 'role', p['role'], 'exp', p['exp'] > p['iat'])"
  ```
- Expect: `sub alice role analyst exp True`.
- [ ] confirmed

### 3.3 THE exit criterion — every dashboard screen fetches real data, roles are enforced, and evidence replay returns the original event bodies
- Run (needs Docker up; seeds the DB, runs the real engine, then drives the API):
  ```powershell
  & $py C:\Users\kmaha\AppData\Local\Temp\opencode\diag_phase6.py
  ```
  (or copy the diagnostic script — full source is in the builder's Phase 6 evidence notes)
- Expect, matching the builder's captured output (`docs/verify_phase6_diags.txt`):
  - `analyst login -> 200`, `wrong password -> 401`, `admin login -> True`
  - **Role separation:** `analyst GET /admin/users -> 403`, `admin GET /admin/users -> 200`
  - **Overview/drill-down (real data):** `open_incidents=1 open_alerts=3 by_band={'Critical': 1}`; incident victim drill-down `current={'risk': 89.0, 'band': 'Critical'}`; a seeded employee drill-down shows `windows=30 baseline_features=7`
  - **Workflow:** assign → `assigned assigned_to=bob`; `force_mfa/revoke_session/isolate_device` all `200 applied(simulated)`; add note `200`; close → `resolved`
  - **Evidence replay:** `3 event bodies, first event_type: login`
  - **Admin:** `create soc2 -> 200 role=analyst`, `soc2 login -> 200`, thresholds `200` with `DORMANCY_DAYS=45 RISK_BAND_CRITICAL=88 RULE_VOLUME_K=6.0`
  - `17 paths` registered in OpenAPI
- [ ] confirmed

### 3.4 Password hashing — PBKDF2, salted, stdlib (no passlib)
- Run:
  ```powershell
  & $py -c "from api.auth import hash_password, verify_password; h = hash_password('x'); print(h.split('$')[0], len(h.split('$')[2]) > 0, verify_password('x', h), not verify_password('y', h))"
  ```
- Expect: `pbkdf2_sha256 True True True`.
- [ ] confirmed

### 3.5 Migration 0004 applied
- Run: `& $py -c "import subprocess, sys; subprocess.run([sys.executable,'-m','alembic','-c','db/alembic.ini','current'], check=True)"`
- Expect: alembic reports `0004_api_settings` as the current head.
- [ ] confirmed

### 3.6 Full auto suite reproducible
- Run: `& $py -m pytest -m "unit or structure or contract" -q`
- Expect: `423 passed`; plus (with Docker up) `& $py -m pytest -m integration -q` → `89 passed`.
- [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.6 confirmed by builder AND user
- [ ] All 17 endpoints from the LLD are registered and served (`/health`, `/auth/*`, `/overview`, `/users[/{id}/risk]`, `/entities[/{id}/risk]`, `/alerts[/{id}]`, `/incidents[/{id}]`, `/incidents/{id}/evidence|actions|notes`, `/admin/users|thresholds`)
- [ ] JWT HS256 auth: valid tokens carry `sub` + `role`; expired/tampered/garbage tokens rejected with 401
- [ ] Password hashing is salted PBKDF2 (stdlib `db/passwords`) — seeded accounts `analyst/analyst`, `admin/admin` log in, wrong passwords get 401
- [ ] RBAC enforced on the API: analysts can read overview/users/entities/alerts/incidents and run the incident workflow; only admins can hit `/admin/*` (403 otherwise)
- [ ] Every dashboard read returns real data: overview aggregates from stored incidents/alerts, drill-downs read stored windows + behavioral baseline + sensitivity context, evidence replay returns the actual `raw_events` bodies referenced by an incident
- [ ] Incident workflow through the API: assign → investigate → simulated response actions → note → resolve, mirroring the Phase 5 lifecycle with auditing
- [ ] Admin can create analyst accounts (instant login) and tune thresholds stored in the `settings` table
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase6_diags.txt` — full API flow: JWT auth, 403 role separation, overview + drill-down (real data), workflow, evidence replay, admin CRUD, OpenAPI paths
- [ ] `docs/verify_phase6_pytest_unit.txt` — pytest summary `423 passed`
- [ ] `docs/verify_phase6_pytest_int.txt` — pytest summary `89 passed`