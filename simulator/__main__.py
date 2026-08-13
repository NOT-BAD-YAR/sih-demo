"""Simulator main orchestration entrypoints.

Usage:
  python -m simulator --backfill 90 --jsonl out/backfill.jsonl
  python -m simulator --scenarios > scenario_events.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone

from .anomaly import inject_scenario
from .backfill import jsonl_sink, run_backfill
from .ground_truth import all_records, clear
from .org import generate_org


def _cmd_backfill(args) -> None:
    org = generate_org(seed=args.seed)
    sink = jsonl_sink(args.jsonl) if args.jsonl else None
    run_backfill(org, days=args.days, events_per_day=args.events_per_day, seed=args.seed, sink=sink)
    print(f"backfill: {args.days} days for {len(org.employees)} employees -> {args.jsonl or 'in-memory'}")


def _cmd_scenarios(args) -> None:
    org = generate_org(seed=args.seed)
    clear()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rng = random.Random(args.seed)
    scenarios = ["volume_spike", "impossible_travel", "out_of_scope", "dormant", "novel_peer", "compromise_chain"]
    all_events = []
    for name in scenarios:
        evs = inject_scenario(org, rng, name, now)
        all_events.extend(evs)
        print(f"{name}: {len(evs)} events; rule={all_records()[-1].rule}", file=sys.stderr)
    for ev in all_events:
        print(ev.to_json)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="simulator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="generate historical normal data")
    b.set_defaults(fn=_cmd_backfill)
    b.add_argument("--days", type=int, default=90)
    b.add_argument("--events-per-day", type=int, default=12)
    b.add_argument("--seed", type=int, default=42)
    b.add_argument("--jsonl")
    s = sub.add_parser("scenarios", help="plant all anomaly scenarios")
    s.set_defaults(fn=_cmd_scenarios)
    s.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()