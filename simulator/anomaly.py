"""Anomaly injection — plants all 5 canonical cases + multi-stage chains.

Each planted scenario returns generated events and writes a ground-truth
record (for Phase 9 evaluation). Scenarios are deterministic per seed.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .ground_truth import record
from .org import Organization, Employee, GEO_CITIES
from .schema import Event, build_event, deterministic_event_id

# far enough to exceed 600 km/h when Δt is minutes
IMPOSSIBLE_TRAVEL_PAIRS = [("Chennai", "Delhi"), ("Mumbai", "Delhi")]


def _target_emp(org: Organization, rng: random.Random, *, non_dormant: bool = True) -> Employee:
    pool = [e for e in org.employees if (not e.dormant) if non_dormant] or org.employees
    return rng.choice(pool)


def _geo(city: str) -> dict:
    lat, lon = GEO_CITIES[city]
    return {"city": city, "lat": lat, "lon": lon}


def _eid(rng: random.Random) -> str:
    return deterministic_event_id(rng)


def inject_scenario(
    org: Organization,
    rng: random.Random,
    scenario: str,
    now: datetime,
) -> list[Event]:
    """Plant `scenario` and return its events (+ a ground-truth record)."""
    if scenario == "volume_spike":
        return _volume_spike(org, rng, now)
    if scenario == "impossible_travel":
        return _impossible_travel(org, rng, now)
    if scenario == "out_of_scope":
        return _out_of_scope(org, rng, now)
    if scenario == "dormant":
        return _dormant(org, rng, now)
    if scenario == "novel_peer":
        return _novel_peer(org, rng, now)
    if scenario == "compromise_chain":
        return _compromise_chain(org, rng, now)
    raise ValueError(f"unknown scenario: {scenario}")


def _volume_spike(org: Organization, rng: random.Random, now: datetime) -> list[Event]:
    emp = _target_emp(org, rng)
    target = sorted(department_resources_of(emp.department))[0]
    spike_events: list[Event] = []
    # 8-20x normal scale: normal is up to 60 MB blobs; spike = several GB total
    for i in range(6):
        gb = rng.randint(1, 9)  # ~5GB target
        ev = build_event(
            entity_type="user",
            entity_id=emp.emp_id,
            user_id=emp.emp_id,
            event_type="download",
            actor=emp.emp_id,
            source_entity=emp.device_id,
            target_entity=target,
            ts=now + timedelta(minutes=i * 3),
            geo=_geo(emp.geo),
            file_path=f"/{emp.department}/{target}/bulk_{i}.zip",
            bytes_moved=int(gb * 1024 * 1024 * 1024) // 6,
            outcome="success",
            sensitivity=org.resource_sensitivity.get(target, "internal"),
            event_id=_eid(rng),
        )
        spike_events.append(ev)
    record(
        scenario="volume_spike",
        entity_id=emp.emp_id,
        start=(now.isoformat()),
        end=(now + timedelta(minutes=15)).isoformat(),
        related_event_ids=[e.event_id for e in spike_events],
        rule="volume_spike",
        expected_risk_band="High",
    )
    return spike_events


def _impossible_travel(org: Organization, rng: random.Random, now: datetime) -> list[Event]:
    emp = _target_emp(org, rng)
    city_a, city_b = IMPOSSIBLE_TRAVEL_PAIRS[0]
    ev_a = build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="login", actor=emp.emp_id, source_entity=emp.device_id,
        target_entity=emp.device_id, ts=now, geo=_geo(city_a), ip="10.1.1.1",
        event_id=_eid(rng),
    )
    ev_b = build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="login", actor=emp.emp_id, source_entity=emp.device_id,
        target_entity=emp.device_id, ts=now + timedelta(minutes=20), geo=_geo(city_b), ip="10.2.2.2",
        event_id=_eid(rng),
    )
    record(
        scenario="impossible_travel",
        entity_id=emp.emp_id,
        start=now.isoformat(),
        end=(now + timedelta(minutes=20)).isoformat(),
        related_event_ids=[ev_a.event_id, ev_b.event_id],
        rule="impossible_travel",
        expected_risk_band="High",
    )
    return [ev_a, ev_b]


def _out_of_scope(org: Organization, rng: random.Random, now: datetime) -> list[Event]:
    emp = _target_emp(org, rng)
    # pick a resource owned by a DIFFERENT department than the employee's
    foreign = [r for r, d in org.resource_owner.items() if d != emp.department]
    if not foreign:
        foreign = list(org.resource_owner)
    target = rng.choice(foreign)
    owning_dept = org.resource_owner[target]
    ev = build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="file_access", actor=emp.emp_id, source_entity=emp.device_id,
        target_entity=target, ts=now, geo=_geo(emp.geo),
        file_path=f"/{owning_dept}/{target}/internal_finance.xlsx",
        bytes_moved=5000,
        outcome="success",
        sensitivity=org.resource_sensitivity.get(target, "internal"),
        event_id=_eid(rng),
    )
    record(
        scenario="out_of_scope",
        entity_id=emp.emp_id,
        start=now.isoformat(),
        end=now.isoformat(),
        related_event_ids=[ev.event_id],
        rule="out_of_scope",
        expected_risk_band="Medium",
    )
    return [ev]


def _dormant(org: Organization, rng: random.Random, now: datetime) -> list[Event]:
    dormant = [e for e in org.employees if e.dormant]
    if not dormant:
        dormant = [org.employees[0]]
    emp = rng.choice(dormant)
    late_night = now.replace(hour=2, minute=30)
    ev = build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="login", actor=emp.emp_id, source_entity=emp.device_id,
        target_entity=emp.device_id, ts=late_night, geo=_geo(emp.geo), ip="10.9.9.9",
        event_id=_eid(rng),
    )
    record(
        scenario="dormant",
        entity_id=emp.emp_id,
        start=late_night.isoformat(),
        end=late_night.isoformat(),
        related_event_ids=[ev.event_id],
        rule="dormant",
        expected_risk_band="Medium",
    )
    return [ev]


def _novel_peer(org: Organization, rng: random.Random, now: datetime) -> list[Event]:
    server = rng.choice(org.servers)
    novel_peer = f"UNKNOWN-{rng.randint(100, 999)}"  # never in server.peers
    ev = build_event(
        entity_type="server",
        entity_id=server.server_id,
        user_id="",
        event_type="network_conn",
        actor=server.server_id,
        source_entity=server.server_id,
        peer_entity=novel_peer,
        ts=now,
        ip="203.0.113.7",
        bytes_moved=rng.randint(1_000_000, 50_000_000),
        outcome="success",
        sensitivity="internal",
        event_id=_eid(rng),
    )
    record(
        scenario="novel_peer",
        entity_id=server.server_id,
        start=now.isoformat(),
        end=now.isoformat(),
        related_event_ids=[ev.event_id],
        rule="novel_peer",
        expected_risk_band="Medium",
    )
    return [ev]


def _compromise_chain(org: Organization, rng: random.Random, now: datetime) -> list[Event]:
    """Multi-stage: login@new-loc → new device → sensitive access → big download → external upload."""
    emp = _target_emp(org, rng)
    foreign = [r for r, d in org.resource_owner.items() if d != emp.department]
    sensitive_target = foreign and rng.choice(foreign) or sorted(department_resources_of(emp.department))[0]
    events = []
    t0 = now

    events.append(build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="login", actor=emp.emp_id, source_entity="THREAT-DEVICE",
        target_entity="THREAT-DEVICE", ts=t0, geo=_geo("Delhi") if emp.geo != "Delhi" else _geo("Mumbai"),
        event_id=_eid(rng),
    ))
    events.append(build_event(
        entity_type="device", entity_id="THREAT-DEVICE", user_id=emp.emp_id,
        event_type="usb", actor="THREAT-DEVICE", source_entity="THREAT-DEVICE",
        target_entity=emp.device_id, ts=t0 + timedelta(minutes=1),
        event_id=_eid(rng),
    ))
    events.append(build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="file_access", actor=emp.emp_id, source_entity="THREAT-DEVICE",
        target_entity=sensitive_target, ts=t0 + timedelta(minutes=3),
        file_path=f"/{org.resource_owner.get(sensitive_target, 'x')}/{sensitive_target}/client_list.xlsx",
        bytes_moved=8_000, sensitivity=org.resource_sensitivity.get(sensitive_target, "restricted"),
        event_id=_eid(rng),
    ))
    events.append(build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="download", actor=emp.emp_id, source_entity="THREAT-DEVICE",
        target_entity="bulk", ts=t0 + timedelta(minutes=8),
        bytes_moved=int(5 * 1024 ** 3),
        event_id=_eid(rng),
    ))
    events.append(build_event(
        entity_type="user", entity_id=emp.emp_id, user_id=emp.emp_id,
        event_type="upload", actor=emp.emp_id, source_entity="THREAT-DEVICE",
        peer_entity="STORAGE.EXTERNAL.CLOUD", ts=t0 + timedelta(minutes=11),
        bytes_moved=int(5 * 1024 ** 3),
        event_id=_eid(rng),
    ))

    record(
        scenario="compromise_chain",
        entity_id=emp.emp_id,
        start=t0.isoformat(),
        end=(t0 + timedelta(minutes=12)).isoformat(),
        related_event_ids=[e.event_id for e in events],
        rule="correlation",
        expected_risk_band="Critical",
    )
    return events


def department_resources_of(dept: str) -> set:
    from .org import DEPARTMENT_RESOURCES

    return DEPARTMENT_RESOURCES.get(dept, set())