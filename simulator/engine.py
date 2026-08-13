"""Event generation engine.

Turns an organization profile into normalized events per the Common Schema.
Deterministic via injected `random.Random`.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .org import GEO_CITIES, Organization, Employee, department_resources
from .schema import Event, build_event, deterministic_event_id


def _active_hour(emp: Employee, rng: random.Random) -> int:
    start, end = emp.active_hours
    if start == end:
        return start
    return rng.randint(start, end)


def generate_normal_event(org: Organization, emp: Employee, rng: random.Random, now: datetime) -> Event:
    """Produce one plausible normal activity event for `emp` at time `now`."""
    dept = emp.department
    event_type = rng.choices(
        ("login", "file_access", "download", "logout", "network_conn", "mfa"),
        weights=(15, 30, 20, 10, 15, 10),
        k=1,
    )[0]

    geo = {"city": emp.geo, "lat": GEO_CITIES[emp.geo][0], "lon": GEO_CITIES[emp.geo][1]}

    common = dict(
        entity_type="user",
        entity_id=emp.emp_id,
        user_id=emp.emp_id,
        actor=emp.emp_id,
        source_entity=emp.device_id,
        ts=now,
        geo=geo,
        ip=f"10.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}",
        event_id=deterministic_event_id(rng),
    )

    resources = sorted(department_resources(dept))
    if not resources:
        resources = ["shared-drive"]

    if event_type in ("file_access", "download"):
        target = rng.choice(resources)
        kb = rng.randint(5, 200) if event_type == "file_access" else rng.randint(*emp.download_scale_mb) * 1024
        return build_event(
            **common,
            event_type=event_type,
            target_entity=target,
            file_path=f"/{dept}/{target}/f{rng.randint(1, 5000)}.xlsx",
            bytes_moved=kb,
            sensitivity=org.resource_sensitivity.get(target, "internal"),
        )

    if event_type == "login":
        return build_event(**common, event_type="login", target_entity=emp.device_id, outcome="success")
    if event_type == "logout":
        return build_event(**common, event_type="logout", target_entity=emp.device_id, outcome="success")
    if event_type == "network_conn":
        peer = rng.choice([s.server_id for s in org.servers])
        return build_event(**common, event_type="network_conn", peer_entity=peer, bytes_moved=rng.randint(10, 1000))
    # default: a benign login-style marker that is schema-valid
    return build_event(**common, event_type="login", target_entity="workspace", outcome="success")


def run_backfill(
    org: Organization,
    days: int = 90,
    events_per_day: int = 12,
    seed: int = 42,
) -> list[Event]:
    """Generate `days` of normal history for all employees.

    Events are spaced across each employee's active window. Returns a flat
    list (bulk path — later phases write to Kafka/DB in batch).
    """
    rng = random.Random(seed)
    events: list[Event] = []
    now = datetime.now(timezone.utc).replace(microsecond=0)
    epoch_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    # start backfill before the 'present' so live phase follows naturally
    start = epoch_day - timedelta(days=days - 1)
    active = [e for e in org.employees if not e.dormant]

    for day_offset in range(days):
        day = start + timedelta(days=day_offset)
        for emp in active:
            s, e = emp.active_hours
            if s == 0 and e == 0:
                continue
            context = {}
            events_today = rng.choices((1, 2, 3), (0.50, 0.30, 0.20))[0]  # 1..3 sessions-ish
            per = max(1, events_per_day // max(1, events_today))
            for _ in range(per):
                hour = rng.randint(s, max(s, e - 1))
                minute = rng.randint(0, 59)
                ts = day.replace(hour=hour, minute=minute)
                if ts < start:
                    continue
                ev = generate_normal_event(org, emp, rng, ts)
                events.append(ev)
    return events


def generate_live_events(org: Organization, rng: random.Random | None = None, now: datetime | None = None, k: int = 5) -> list[Event]:
    """Generate `k` events at the current moment for a random selection of employees."""
    rng = rng or random.Random()
    active = [e for e in org.employees if not e.dormant]
    chosen = [rng.choice(active) for _ in range(k)]
    events: list[Event] = []
    for emp in chosen:
        if not (emp.active_hours[0] <= now.hour <= emp.active_hours[1]):
            continue
        events.append(generate_normal_event(org, emp, rng, now))
    return events


def assert_all_valid(events: list[Event]) -> None:
    """Guard: raise on any schema-invalid event."""
    for ev in events:
        problems = _validate_or_nothing(ev)
        if problems:
            raise ValueError(f"invalid event: {problems}")


def _validate_or_nothing(ev: Event) -> list:
    from .schema import validate

    return validate(ev)