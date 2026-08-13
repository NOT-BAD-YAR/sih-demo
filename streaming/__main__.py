"""Streaming pipeline CLI (Phase 2) — health, topic provisioning, end-to-end demo.

Usage:
  python -m streaming ensure-topics --bootstrap localhost:9092
  python -m streaming demo --bootstrap localhost:9092 --events 8
  python -m streaming demo --group demo-group
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone


def _cmd_ensure_topics(args) -> None:
    from .admin import ensure_topics

    print("topics:", ensure_topics(args.bootstrap))


def _cmd_demo(args) -> None:
    from .monitor import health, consumer_lag
    from .admin import ensure_topics

    print("== kafka health ==")
    h = health(args.bootstrap)
    print("health:", h)
    if not h["kafka"]:
        print("ERROR: Kafka unreachable. Start infra first:  docker compose up -d kafka")
        sys.exit(1)

    print("\n== ensure topics ==")
    print("topics:", ensure_topics(args.bootstrap))

    print("\n== build events ==")
    from simulator.schema import build_event
    from .producer import EventProducer, normalize_payload
    from .dedupe import IdempotentHandler
    from .consumer import EngineConsumer

    events = []
    for i in range(args.events):
        ev = build_event(
            entity_type="user",
            entity_id=f"EMP{i:03d}",
            user_id=f"EMP{i:03d}",
            event_type=("login" if i % 2 == 0 else "download"),
            actor=f"EMP{i:03d}",
            source_entity=f"LPT-{i:03d}",
            target_entity="share",
            ts=datetime.now(timezone.utc),
        )
        events.append(normalize_payload(ev))

    target_ids = {e["event_id"] for e in events}
    applied: list[dict] = []

    def collect(payload: dict) -> None:
        if payload["event_id"] in target_ids:
            applied.append(payload)

    idem = IdempotentHandler(collect)
    consumer = EngineConsumer(
        args.bootstrap, args.group, ["auth-events"], handler=idem, auto_offset_reset="latest"
    )
    try:
        joined = False
        for _ in range(30):  # join the group before messages land
            consumer.poll_once(timeout=0.5)
            if consumer._consumer.assignment():
                joined = True
                break
        print("consumer joined group:", joined)

        print("\n== produce (incl. 1 redelivered copy of the first event) ==")
        producer = EventProducer(args.bootstrap, "auth-events")
        # second copy of events[0] simulates a Kafka redelivery (at-least-once)
        messages = events + [dict(events[0])]
        for msg in messages:
            producer.send(msg)
        remaining = producer.flush(timeout=15)
        print(f"produced {len(messages)} messages, flush remaining={remaining}")

        for _ in range(1000):
            if len(applied) >= args.events and idem.rejected >= 1:
                break
            consumer.poll_once(timeout=0.2)
    finally:
        consumer.close()

    print("\n== consume (via IdempotentHandler) ==")
    print(f"applied unique events : {len(applied)} / {args.events}")
    print(f"rejected duplicates   : {idem.rejected}")
    if len(applied) != args.events or idem.rejected != 1:
        print("ERROR: end-to-end demo result mismatch")
        sys.exit(1)
    print("first applied:", {k: applied[0].get(k) for k in ("event_id", "event_type", "user_id")})

    print("\n== lag report ==")
    print("lag:", consumer_lag(args.bootstrap, args.group, ["auth-events"]))


def _cmd_roundtrip(args) -> None:
    """Produce one auth event and consume it back — proves simulator->Kafka->consumer."""
    from datetime import datetime, timezone
    from simulator.schema import build_event
    from .producer import EventProducer, normalize_payload
    from .consumer import EngineConsumer

    ev = build_event(
        entity_type="user", entity_id="RT002", user_id="RT002", event_type="login",
        actor="RT002", source_entity="LPT-002", target_entity="LPT-002",
        ts=datetime.now(timezone.utc),
    )
    payload = normalize_payload(ev)

    seen: list[dict] = []
    consumer = EngineConsumer(args.bootstrap, args.group, ["auth-events"], handler=seen.append, auto_offset_reset="latest")
    try:
        for _ in range(30):
            consumer.poll_once(timeout=0.5)
            if consumer._consumer.assignment():
                break
        producer = EventProducer(args.bootstrap, "auth-events")
        producer.send(payload)
        producer.flush(timeout=10)
        for _ in range(100):
            if any(p.get("event_id") == payload["event_id"] for p in seen):
                break
            consumer.poll_once(timeout=0.5)
    finally:
        consumer.close()

    got = next((p for p in seen if p.get("event_id") == payload["event_id"]), None)
    print("produced event_id :", payload["event_id"])
    print("consumed          :", got is not None)
    print("event_type matches:", got is not None and got["event_type"] == "login")
    if got is None:
        sys.exit(1)


def _cmd_dedupe(args) -> None:
    """Produce the same event_id twice (simulated redelivery); assert only 1 applied."""
    from datetime import datetime, timezone
    from simulator.schema import build_event
    from .producer import EventProducer, normalize_payload
    from .dedupe import IdempotentHandler
    from .consumer import EngineConsumer

    ev = build_event(
        entity_type="user", entity_id="DP003", user_id="DP003", event_type="mfa",
        actor="DP003", source_entity="LPT-003", target_entity="LPT-003",
        ts=datetime.now(timezone.utc),
    )
    payload = normalize_payload(ev)

    applied: list[dict] = []
    idem = IdempotentHandler(applied.append)
    consumer = EngineConsumer(args.bootstrap, args.group, ["auth-events"], handler=idem, auto_offset_reset="latest")
    try:
        for _ in range(30):
            consumer.poll_once(timeout=0.5)
            if consumer._consumer.assignment():
                break
        producer = EventProducer(args.bootstrap, "auth-events")
        producer.send(payload)
        producer.send(dict(payload))  # redelivered copy
        producer.flush(timeout=10)
        for _ in range(100):
            if idem.processed >= 1 and idem.rejected >= 1:
                break
            consumer.poll_once(timeout=0.5)
    finally:
        consumer.close()

    print("delivered twice?   :", idem.processed + idem.rejected == 2)
    print("applied unique     :", len(applied))
    print("duplicates rejected:", idem.rejected)
    if len(applied) != 1 or idem.rejected != 1:
        sys.exit(1)


def _cmd_persist(args) -> None:
    """Produce 1 event + its redelivered copy and persist through the DB consumer.

    Proves the Phase 3 exit gate end-to-end: simulator payload -> Kafka ->
    consumer -> raw_events, with the duplicate absorbed by ON CONFLICT.
    """
    from datetime import datetime, timezone
    from simulator.schema import build_event
    from .producer import EventProducer, normalize_payload
    from .consumer import EngineConsumer
    from .admin import ensure_topics
    from db.persist import build_persist_handler

    ensure_topics(args.bootstrap)

    ev = build_event(
        entity_type="user", entity_id="PS004", user_id="PS004", event_type="login",
        actor="PS004", source_entity="LPT-004", target_entity="LPT-004",
        ts=datetime.now(timezone.utc), geo={"city": "Bangalore", "lat": 12.97, "lon": 77.59},
    )
    payload = normalize_payload(ev)

    from db.conn import connect
    from db.dao import get_event, count_events

    before = None
    with connect() as conn:
        before = count_events(conn)

    outcomes: list[bool] = []
    handler = build_persist_handler()

    def collect(p: dict) -> None:
        outcomes.append(handler(p))

    consumer = EngineConsumer(args.bootstrap, args.group, ["auth-events"], handler=collect, auto_offset_reset="latest")
    try:
        for _ in range(30):
            consumer.poll_once(timeout=0.5)
            if consumer._consumer.assignment():
                break
        producer = EventProducer(args.bootstrap, "auth-events")
        producer.send(payload)
        producer.send(dict(payload))  # redelivered copy of the same event_id
        producer.flush(timeout=10)
        for _ in range(100):
            if len(outcomes) >= 2:
                break
            consumer.poll_once(timeout=0.5)
    finally:
        consumer.close()

    with connect() as conn:
        after = count_events(conn)
        row = get_event(conn, payload["event_id"])

    print("produced             : 2 messages (same event_id) -> auth-events")
    print("persist outcomes     :", outcomes)                          # [True, False]
    print("raw_events before    :", before)
    print("raw_events after     :", after)
    print("net_new_rows         :", after - before)                    # exactly 1
    print("persisted event      :", row is not None)
    print("persisted event_type :", row.get("event_type") if row else None)
    print("persisted geo city   :", row.get("geo", {}).get("city") if row else None)
    if outcomes != [True, False] or after - before != 1 or row is None:
        print("ERROR: persistence / dedupe result mismatch")
        sys.exit(1)


def _cmd_lag(args) -> None:
    from .monitor import health, consumer_lag

    h = health(args.bootstrap)
    if not h["kafka"]:
        print("ERROR: Kafka unreachable. Start infra first:  docker compose up -d kafka")
        sys.exit(1)
    topics = list(h["topics"])
    print("lag for group", args.group, "on", topics)
    print("lag:", consumer_lag(args.bootstrap, args.group, topics))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="streaming")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, fn in (
        ("ensure-topics", _cmd_ensure_topics),
        ("roundtrip", _cmd_roundtrip),
        ("dedupe", _cmd_dedupe),
        ("persist", _cmd_persist),
        ("demo", _cmd_demo),
        ("lag", _cmd_lag),
    ):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        p.add_argument("--bootstrap", default="localhost:9092")
        p.add_argument("--group", default="streaming-demo-group")
        p.add_argument("--events", type=int, default=8)
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()