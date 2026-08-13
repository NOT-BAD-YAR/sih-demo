# MANUAL VERIFICATION — Phase 7 (Multi-User SOC Dashboard · React + TypeScript)

> Run by BOTH the builder and the user independently. Phase 7 is PASS only
> when every checkbox is completed and both parties' results match.

## 1. Prerequisites

- [ ] Builder's machine: full auto suite passed (`512 passed`), frontend gates green (typecheck, build, lint, `20 passed`), and the E2E screenshot harness passed (exit 0)
- [ ] User has a separate verification session to reproduce these steps
- [ ] Docker + Compose up (Postgres + Kafka), API running on `:8000`, Vite dev on `:5173`
- [ ] Demo seed run: `& .\.venv\Scripts\python.exe -X utf8 scripts\seed_demo.py` (1 open incident #1 EMP045 Critical, 3 open alerts, 100 users, accounts analyst/analyst · admin/admin · soc1/analyst123)
- [ ] Browser: Edge or Chrome (E2E uses the installed Edge channel `msedge`)

## 2. Start the stack

```powershell
# terminal 1 — API (seeded DB already loaded)
& .\.venv\Scripts\python.exe -X utf8 -m uvicorn api.main:app --port 8000

# terminal 2 — dashboard
Set-Location dashboard; npm run dev    # -> http://localhost:5173
```

Open **http://localhost:5173** — you should land on the login screen.

## 3. Step-by-step checks (screenshots in `docs/verify_phase7_screens/`)

### 3.1 Login + Overview
- Log in as `analyst / analyst`.
- Expect the **Overview**: `Open incidents 1`, `Open alerts 3`, `Total open risk`, `Live feed polling (refreshes every 15 s)`, a `Risk by band` panel with **Critical 1**, and `Top users/entities at risk` (EMP045).
- Screenshot `01-overview.png`. [ ] confirmed

### 3.2 Users list + Entity Investigation (flagship)
- Nav → **Users** → search `EMP001` → click the row.
- Expect the investigation page sections: **Normal behavior (baseline snapshot)** (30 windows, baseline features), **Current behavior**, deviation/reason, **Why flagged?** card, **risk timeline**, and linked incidents.
- Screenshots `02-users.png`, `03-investigation-emp001.png`. [ ] confirmed
- Nav → **Entities** → screenshot `04-entities.png`. [ ] confirmed

### 3.3 THE exit criterion — analyst works an incident end-to-end from the UI
- Nav → **Incidents** → open **#1 (EMP045 Critical)**.
- Expect: detail header (status pill `open`, Critical risk), entity/assignee meta, **Entity chain**, **Simulated response**, **Analyst notes**, and **Evidence replay**.
- Click **Assign** → pill becomes `Assigned` (self-assigned to `analyst`).
- Click **Investigate** → pill becomes `Investigating`.
- Apply simulated responses **Force MFA**, **Isolate device**, **Revoke session** → each shows in the **Audit trail** as `applied`.
- Add a note (`evidence reviewed, response complete`) → appears in **Analyst notes**.
- Click **Resolve** → pill becomes `Resolved`.
- Screenshots `05-incident-open.png`, `06-incident-actions-evidence.png`, `07-incident-resolved.png`.
- [ ] confirmed

### 3.4 Alert queue
- Nav → **Alerts** → expect the queue with `3 open`.
- Click **False positive** on the first open alert → it moves to the closed set (flash message shown).
- Screenshot `08-alerts.png`. [ ] confirmed

### 3.5 Live updates via polling (no refresh)
- Nav → **Overview**; note the **Open alerts** count (read **2** after step 3.4; or current value).
- In a shell run `& .\.venv\Scripts\python.exe -X utf8 scripts\inject_live.py` (plants a fresh volume-spike alert through the real engine).
- Do **not** refresh. Within one poll tick (~15 s) the **Open alerts** count rises by 1.
- Screenshot `09-overview-live-update.png`. [ ] confirmed

### 3.6 Admin (separate account + role separation)
- **Sign out**, log in as `admin / admin`.
- Nav → **Admin** → **Manage users**: create account `soc2` (role analyst, password `pass1234`) → success message; the account appears in the list.
- Switch to **Thresholds**: set `RISK_BAND_CRITICAL` to `80` → **Save thresholds** → `Thresholds updated`.
- Screenshots `10-admin-users.png`, `11-admin-thresholds.png`. [ ] confirmed

### 3.7 Role enforcement (bonus check)
- Sign out; as `analyst` navigate to **Admin** in the URL (or menu) — expect a **403 access denied** state, not the admin UI. [ ] confirmed

## 4. Success criteria

- [ ] 3.1–3.7 confirmed by builder AND user
- [ ] All screens read real API data (no placeholders/errors): Overview aggregates, Users/Entities lists + drill-downs, Alerts, Incidents + detail, Admin
- [ ] Entity Investigation shows normal → current → deviation → reason → risk timeline → incidents for a seeded employee
- [ ] Incident lifecycle driven fully from the UI: assign → investigate → simulated actions → note → resolve, with audit trail + evidence replay
- [ ] Alerts triaged from the UI (false positive path)
- [ ] New anomalies appear **live** via polling without a page refresh
- [ ] Admin manages accounts + thresholds; analysts are blocked from admin areas (403)
- [ ] No unexplained failures; any discrepancy documented and fixed before closing

## 5. Evidence captured

- [ ] `docs/verify_phase7_screens/` — 11 PNG screenshots covering every screen + the live-update transition
- [ ] `docs/verify_phase7_diags.txt` — E2E harness log: all steps pass, `open alerts rose 2 -> 3 on Overview without refresh`
- [ ] `docs/verify_phase7_pytest_full.txt` — pytest summary `512 passed`
- [ ] `docs/verify_phase7_frontend.txt` — typecheck / build / lint / `20 passed` vitest results