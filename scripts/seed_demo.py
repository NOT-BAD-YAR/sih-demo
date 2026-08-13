# -*- coding: utf-8 -*-
"""Seed the SOC demo: real backfill + a compromise-chain incident + a rich
individual drill-down, so every Phase 7 dashboard screen has real data.

Usage:  .venv\\Scripts\\python.exe -X utf8 scripts\\seed_demo.py
Requires: docker compose postgres (and kafka) healthy, alembic at head.
"""

import random
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

NOW = datetime(2026, 2, 1, 10, 30, tzinfo=timezone.utc)
DSN = "postgresql://ueba:ueba_secret@localhost:5432/ueba"


def _healthy(service: str) -> bool:
    out = subprocess.run(
        ["docker", "compose", "ps", "--format", "json"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    for line in out.splitlines():
        try:
            info = __import__("json").loads(line)
            if info.get("Service") == service and info.get("Health") == "healthy":
                return True
        except Exception:
            continue
    return False


def main() -> None:
    subprocess.run(["docker", "compose", "up", "-d", "postgres", "kafka"], cwd=ROOT)
    deadline = time.time() + 150
    while time.time() < deadline and not (_healthy("postgres") and _healthy("kafka")):
        time.sleep(5)
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "db/alembic.ini", "upgrade", "head"],
        cwd=ROOT, check=True,
    )

    from db.conn import connect
    from db.dao import insert_event, upsert_window, upsert_profile
    from analytics.baseline import build_individual

    conn = connect(DSN)
    with conn.cursor() as cur:
        cur.execute(
            "TRUNCATE settings, alerts, incidents, analyst_actions, feature_windows, "
            "behavioral_profiles, raw_events, ground_truth, users_accounts, entities, "
            "users, peer_groups RESTART IDENTITY CASCADE"
        )
        conn.commit()

    from simulator.org import generate_org
    from simulator.engine import run_backfill
    from simulator.anomaly import inject_scenario
    from streaming.producer import normalize_payload
    from analytics.processor import validate
    from analytics.features import accumulate_all, finalize
    from analytics.ml import train, clear_models
    from analytics.runner import AnalyticsRunner
    from db.seed import seed_org, seed_accounts

    org = generate_org(seed=42)
    seed_org(conn, org)
    seed_accounts(conn, {"analyst": "analyst", "admin": "admin", "soc1": "analyst123"})

    back = [
        n for n in (
            validate(normalize_payload(e))
            for e in run_backfill(org, days=30, events_per_day=12, seed=42)
        )
        if n
    ]
    by_entity: dict[str, list] = defaultdict(list)
    for w in accumulate_all(back).values():
        by_entity[w.entity_ref].append(finalize(w))

    clear_models()
    train("global", "__global__", [v for vs in by_entity.values() for v in vs], force=True)

    # compromise chain -> ONE incident with evidence + alerts + entity chain
    runner = AnalyticsRunner(store=conn, org=org, history=dict(by_entity))
    for e in inject_scenario(org, random.Random(7), "compromise_chain", NOW):
        payload = normalize_payload(e)
        insert_event(conn, payload)
        runner.on_event(payload)
    runner.flush()

    # rich individual drill-down for the first employee
    emp = org.employees[0]
    for w in by_entity[emp.emp_id][:30]:
        upsert_window(conn, emp.emp_id, datetime.fromisoformat(w["window_start"]), w)
    upsert_profile(conn, build_individual(emp.emp_id, by_entity[emp.emp_id][:30]))

    from db.dao import get_incidents, get_alerts, get_users

    incidents = get_incidents(conn, status="open")
    alerts = get_alerts(conn)
    users = get_users(conn)
    conn.close()

    print("=== demo seed complete ===")
    print(f"open incidents : {len(incidents)}")
    for i in incidents:
        print(f"  #{i['id']} {i['entity_ref']} {i['severity']} risk={i['risk']} "
              f"evidence={len(i.get('evidence_refs') or [])} chain={len(i.get('entity_chain') or [])}")
    print(f"alerts         : {len(alerts)} (open={sum(1 for a in alerts if a['status'] not in ('resolved','false_positive'))})")
    print(f"users          : {len(users)} (first: {users[0]['emp_id']})")
    print("accounts       : analyst/analyst, admin/admin, soc1/analyst123")


if __name__ == "__main__":
    main()
