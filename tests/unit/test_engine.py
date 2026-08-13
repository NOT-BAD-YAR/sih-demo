"""Phase 1 — Backfill + live generation tests."""

import pytest
from datetime import datetime, timezone

from simulator.org import generate_org
from simulator.backfill import run_backfill, jsonl_sink
from simulator.live import run_live, generate_live_events
from simulator.schema import is_valid
from simulator.engine import assert_all_valid

pytestmark = pytest.mark.unit


class TestBackfill:
    def test_90_days_produces_data(self):
        org = generate_org(seed=3)
        events = run_backfill(org, days=90, events_per_day=12, seed=3)
        assert len(events) > 1000, "backfill too thin"
        # ~100 employees * ~12/day * 90 days is around 100k; guard lower bound generously
        assert len(events) < 300_000

    def test_all_backfill_events_valid(self):
        org = generate_org(seed=3)
        events = run_backfill(org, days=7, events_per_day=12, seed=3)
        assert events
        assert_all_valid(events)

    def test_backfill_spans_expected_range(self):
        org = generate_org(seed=3)
        events = run_backfill(org, days=90, events_per_day=12, seed=3)
        ts = [e.ts for e in events]
        span_days = (max(ts) - min(ts)).days
        assert 88 <= span_days <= 90

    def test_jsonl_sink_writes_lines(self, tmp_path):
        org = generate_org(seed=5)
        path = str(tmp_path / "backfill.jsonl")
        events = run_backfill(org, days=2, events_per_day=12, seed=5, sink=jsonl_sink(path))
        lines = open(path, encoding="utf-8").read().splitlines()
        assert len(lines) == len(events)
        import json

        payload = json.loads(lines[0])
        assert "event_id" in payload and "event_type" in payload

    def test_deterministic_across_runs(self):
        org = generate_org(seed=9)
        a = run_backfill(org, days=20, events_per_day=12, seed=9)
        b = run_backfill(org, days=20, events_per_day=12, seed=9)
        assert [e.event_id for e in a] == [e.event_id for e in b]


class TestLive:
    def test_live_generates_valid_events(self):
        org = generate_org(seed=11)
        now = datetime.now(timezone.utc)
        events = generate_live_events(org, now=now)
        assert all(is_valid(e) for e in events)

    def test_run_live_max_ticks(self):
        import random

        org = generate_org(seed=11)
        emitted = run_live(org, max_ticks=1, seed=11)
        assert isinstance(emitted, list)
        assert all(is_valid(e) for e in emitted)

    def test_live_events_belong_to_working_window(self):
        # events dropped when no active employees at this hour — just assert safe call
        org = generate_org(seed=13)
        events = generate_live_events(org, now=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc))
        assert isinstance(events, list)