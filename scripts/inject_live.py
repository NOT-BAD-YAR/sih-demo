# -*- coding: utf-8 -*-
"""Inject a NEW volume-spike anomaly through the engine (live demo).

Run while the dashboard is open on Overview: within one polling tick the
open-alert count increases with NO page refresh. Requires postgres + kafka up
and a prior `scripts/seed_demo.py` run.

Usage:  .venv\\Scripts\\python.exe -X utf8 scripts\\inject_live.py
"""

import random
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DSN = "postgresql://ueba:ueba_secret@localhost:5432/ueba"


def main() -> None:
    subprocess.run(["docker", "compose", "up", "-d", "postgres", "kafka"], cwd=ROOT)

    from db.conn import connect
    from db.dao import insert_event

    conn = connect(DSN)

    from simulator.org import generate_org
    from simulator.engine import run_backfill
    from simulator.anomaly import inject_scenario
    from streaming.producer import normalize_payload
    from analytics.processor import validate
    from analytics.features import accumulate_all, finalize
    from analytics.ml import train, clear_models
    from analytics.runner import AnalyticsRunner

    org = generate_org(seed=42)
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

    runner = AnalyticsRunner(store=conn, org=org, history=dict(by_entity))
    now = datetime(2026, 2, 1, 11, 0, tzinfo=timezone.utc)
    for e in inject_scenario(org, random.Random(101), "volume_spike", now):
        payload = normalize_payload(e)
        insert_event(conn, payload)
        runner.on_event(payload)
    runner.flush()

    from db.dao import get_alerts

    alerts = get_alerts(conn)
    conn.close()
    print("=== live injection complete ===")
    print(f"total alerts now: {len(alerts)}")
    for a in alerts[:3]:
        print(f"  #{a['id']} {a['entity_ref']} {a['severity']} risk={a['risk']} status={a['status']}")


if __name__ == "__main__":
    main()
