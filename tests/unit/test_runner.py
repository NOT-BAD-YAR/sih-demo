"""Phase 4F — Runner orchestration unit tests (pure, no Docker/DB).

Drives the AnalyticsRunner end-to-end with real (simulated) baseline history:

  * ingest: valid events accumulate, invalid payloads are dropped, windows
    close on hour-bucket rollover and at flush();
  * detection: volume_spike, dormant activation, novel-peer cold starts and
    the multi-stage Account Compromise chain each surface as ONE defensible
    incident;
  * normal (in-distribution) traffic stays silent — no rule fires and the ML
    signal is below the ML-only threshold;
  * cold-start entities (no baseline) are never judged harshly;
  * run() without a bound consumer raises.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

import pytest

from simulator.org import generate_org
from simulator.engine import run_backfill
from simulator.schema import build_event
from streaming.producer import normalize_payload
from analytics.processor import validate
from analytics.features import accumulate_all, finalize
from analytics.ml import train
from analytics.runner import AnalyticsRunner, ML_ONLY_THRESHOLD

NOW = datetime(2026, 2, 1, 10, 30, tzinfo=timezone.utc)
BANDS = {"Low", "Medium", "High", "Critical"}


@pytest.fixture(scope="module")
def baseline():
    org = generate_org(seed=42)
    raw = run_backfill(org, days=14, events_per_day=12, seed=42)
    norm = [n for n in (validate(normalize_payload(e)) for e in raw) if n]
    by_entity = defaultdict(list)
    for w in accumulate_all(norm).values():
        by_entity[w.entity_ref].append(finalize(w))
    train("global", "__global__", [v for vs in by_entity.values() for v in vs], force=True)
    return {"org": org, "raw": raw, "by_entity": dict(by_entity)}


def _wire(entity_type="user", entity_id="u1", event_type="login", actor="u1",
          ts=NOW, source_entity="dev1", target_entity="dev1", peer_entity="",
          bytes_moved=0, sensitivity="internal", geo=None, event_id=None,
          ip="10.0.0.1", file_path=None):
    ev = build_event(
        entity_type=entity_type, entity_id=entity_id, user_id=entity_id,
        event_type=event_type, actor=actor, ts=ts,
        source_entity=source_entity, target_entity=target_entity,
        peer_entity=peer_entity, ip=ip, geo=geo or {"city": "Delhi", "lat": 28.6, "lon": 77.2},
        file_path=file_path, bytes_moved=bytes_moved, sensitivity=sensitivity,
        event_id=event_id,
    )
    return normalize_payload(ev)


def _synthetic_history():
    return [
        {
            "entity_ref": "HIST",
            "window_start": (NOW - timedelta(days=30 - i)).isoformat(),
            "volume": 50_000_000,
            "event_count": 12,
            "active_hours_frac": 0.5,
            "unique_peers": ["SRV-01", "SRV-02"],
            "new_peer_count": 0,
            "location_count": 1,
            "location_dist_km": 0.0,
            "dept_distinct": ["HR"],
            "sensitivity_hist": {"internal": 10, "confidential": 2},
            "fail_rate": 0.0,
            "staleness_days": 0,
        }
        for i in range(30)
    ]


class TestIngest:
    def test_valid_event_accumulates(self):
        runner = AnalyticsRunner()
        out = runner.on_event(_wire(event_id="e1"))
        assert out is not None and out.event_id == "e1"
        assert runner.stats["events"] == 1 and runner.stats["dropped"] == 0

    def test_invalid_payload_dropped(self):
        runner = AnalyticsRunner()
        assert runner.on_event({}) is None
        assert runner.stats["dropped"] == 1

    def test_bucket_rollover_closes_window(self):
        runner = AnalyticsRunner()
        runner.on_event(_wire(event_id="a", ts=NOW))
        runner.on_event(_wire(event_id="b", ts=NOW + timedelta(hours=1)))
        assert runner.stats["windows_closed"] == 1

    def test_flush_closes_open_windows(self):
        runner = AnalyticsRunner()
        runner.on_event(_wire(event_id="a", ts=NOW))
        assert runner.stats["windows_closed"] == 0
        assert runner.flush() == 1
        assert runner.stats["windows_closed"] == 1

    def test_run_requires_bound_consumer(self):
        with pytest.raises(RuntimeError):
            AnalyticsRunner().run()

    def test_ml_only_threshold_is_sane(self):
        assert 0.0 < ML_ONLY_THRESHOLD < 1.0


class TestNormalTrafficStaysSilent:
    def test_in_distribution_hour_produces_no_incident(self, baseline):
        emp = baseline["org"].employees[0]
        emp_events = [e for e in baseline["raw"] if getattr(e, "entity_id", None) == emp.emp_id]
        assert emp_events, "expected real backfill events for the emp"

        runner = AnalyticsRunner(org=baseline["org"], history=baseline["by_entity"])
        for e in emp_events[:12]:
            runner.on_event(normalize_payload(e))
        runner.flush()

        assert runner.stats["incidents"] == 0
        assert runner.stats["alerts"] == 0


class TestVolumeSpike:
    def test_spike_detected_critical(self, baseline):
        emp = baseline["org"].employees[0]
        runner = AnalyticsRunner(org=baseline["org"], history=baseline["by_entity"])
        for i in range(6):
            runner.on_event(_wire(
                entity_id=emp.emp_id, actor=emp.emp_id, source_entity=emp.device_id,
                target_entity="build-server", event_type="download",
                bytes_moved=int(2 * 1024 ** 3), sensitivity="restricted",
                ts=NOW + timedelta(minutes=i * 3), event_id=f"spike-{i}",
                file_path="/Developers/build-server/bulk.zip"))
        runner.flush()

        assert runner.stats["incidents"] == 1
        inc = runner._open_incidents[0]
        assert inc.risk >= 50.0, inc
        assert inc.severity in {"High", "Critical"}, inc
        assert inc.evidence_refs


class TestDormant:
    def test_dormant_activation_detected(self, baseline):
        org = baseline["org"]
        emp = next(e for e in org.employees if e.dormant)
        runner = AnalyticsRunner(org=org, history={emp.emp_id: _synthetic_history()})

        runner.on_event(_wire(entity_id=emp.emp_id, actor=emp.emp_id,
                              source_entity=emp.device_id, target_entity=emp.device_id,
                              event_type="login", ts=NOW.replace(hour=2, minute=30),
                              event_id="dorm-1"))
        runner.flush()

        assert runner.stats["incidents"] == 1
        inc = runner._open_incidents[0]
        assert 25.0 <= inc.risk < 75.0, inc
        assert inc.severity in {"Medium", "High"}, inc


class TestColdStartGentle:
    def test_novel_peer_on_fresh_server_never_harsh(self, baseline):
        srv = baseline["org"].servers[0]
        runner = AnalyticsRunner(org=baseline["org"])

        runner.on_event(_wire(entity_type="server", entity_id=srv.server_id,
                              actor=srv.server_id, source_entity=srv.server_id,
                              target_entity="crm_db", event_type="network_conn",
                              peer_entity="UNKNOWN-999", event_id="np-1",
                              ts=NOW, sensitivity="confidential"))
        runner.flush()

        assert runner.stats["incidents"] == 1
        inc = runner._open_incidents[0]
        assert inc.risk <= 50.0, f"cold-start entity judged harshly: {inc}"
        assert inc.severity in {"Low", "Medium"}


class TestCompromiseChainFoldsToOne:
    def test_chain_folds_into_one_incident(self, baseline):
        org = baseline["org"]
        emp = org.employees[0]
        foreign = next(r for r, d in org.resource_owner.items() if d != emp.department)
        runner = AnalyticsRunner(org=org, history=baseline["by_entity"])

        t0 = NOW
        chain = [
            _wire(entity_id=emp.emp_id, actor=emp.emp_id, source_entity="THREAT-DEVICE",
                  target_entity="THREAT-DEVICE", event_type="login", ts=t0, event_id="c1",
                  geo={"city": "Delhi", "lat": 28.6, "lon": 77.2}),
            _wire(entity_type="device", entity_id="THREAT-DEVICE", actor="THREAT-DEVICE",
                  source_entity="THREAT-DEVICE", target_entity=emp.device_id,
                  event_type="usb", ts=t0 + timedelta(minutes=1), event_id="c2"),
            _wire(entity_id=emp.emp_id, actor=emp.emp_id, source_entity="THREAT-DEVICE",
                  target_entity=foreign, event_type="file_access", ts=t0 + timedelta(minutes=3),
                  event_id="c3", sensitivity="confidential",
                  file_path=f"/{org.resource_owner[foreign]}/{foreign}/client_list.xlsx"),
            _wire(entity_id=emp.emp_id, actor=emp.emp_id, source_entity="THREAT-DEVICE",
                  target_entity="bulk", event_type="download",
                  bytes_moved=int(5 * 1024 ** 3), ts=t0 + timedelta(minutes=8), event_id="c4",
                  sensitivity="restricted"),
            _wire(entity_id=emp.emp_id, actor=emp.emp_id, source_entity="THREAT-DEVICE",
                  target_entity="bulk", peer_entity="STORAGE.EXTERNAL.CLOUD",
                  event_type="upload", bytes_moved=int(5 * 1024 ** 3),
                  ts=t0 + timedelta(minutes=11), event_id="c5", sensitivity="restricted"),
        ]
        for p in chain:
            runner.on_event(p)
        runner.flush()

        assert len(runner._open_incidents) == 1, runner._open_incidents
        inc = runner._open_incidents[0]
        assert inc.risk >= 50.0, inc
        assert inc.severity in {"High", "Critical"}, inc
        assert len(inc.evidence_refs) >= 3, inc.evidence_refs
        assert "STORAGE.EXTERNAL.CLOUD" in inc.entity_chain
        assert "THREAT-DEVICE" in inc.entity_chain
        assert inc.updated_at - inc.created_at <= timedelta(minutes=30)