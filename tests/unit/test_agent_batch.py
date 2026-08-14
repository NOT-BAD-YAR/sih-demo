"""Phase 8 — BatchBuffer flushes on count threshold, time threshold, and retries."""

import pytest
from datetime import datetime, timezone

from agents.windows_agent.batch import BatchBuffer, BatchFlushError, run_batch
from simulator.schema import build_event

pytestmark = pytest.mark.unit


def _event(i: int):
    return build_event(
        entity_type="device", entity_id="DESKTOP-X", user_id=f"u{i}", event_type="file_access",
        actor=f"u{i}", source_entity="DESKTOP-X", target_entity=f"C:\\f{i}.txt",
        ts=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    )


class TestCountFlush:
    def test_flushes_when_size_reached(self):
        sent: list[list[dict]] = []
        buf = BatchBuffer(sent.append, max_size=3, flush_interval_sec=60.0)
        buf.add(_event(1))
        buf.add(_event(2))
        assert buf.pending == 2 and sent == []  # below threshold, buffered
        buf.add(_event(3))
        assert buf.pending == 0
        assert len(sent) == 1 and len(sent[0]) == 3

    def test_flush_returns_sent_count(self):
        sent: list[list[dict]] = []
        buf = BatchBuffer(sent.append, max_size=10, flush_interval_sec=60.0)
        for i in range(4):
            buf.add(_event(i))
        assert buf.flush() == 4
        assert buf.stats()["sent"] == 4

    def test_event_objects_normalized_to_wire_dicts(self):
        sent: list[list[dict]] = []
        buf = BatchBuffer(sent.append, max_size=2)
        buf.add(_event(1))
        buf.add(_event(2))
        assert len(sent) == 1
        for payload in sent[0]:
            assert isinstance(payload, dict)
            assert payload["event_id"] and payload["bytes"] == 0  # canonical field


class TestTimeFlush:
    def test_tick_flushes_aged_buffer(self):
        sent: list[list[dict]] = []
        clock = {"t": 0.0}
        buf = BatchBuffer(
            sent.append, max_size=100, flush_interval_sec=5.0, now=lambda: clock["t"]
        )
        buf.add(_event(1))
        buf.add(_event(2))
        clock["t"] = 4.9
        buf.tick()
        assert buf.pending == 2  # not aged yet
        clock["t"] = 5.0
        buf.tick()
        assert buf.pending == 0
        assert len(sent) == 1 and len(sent[0]) == 2

    def test_tick_noop_when_empty(self):
        sent = []
        buf = BatchBuffer(sent.append, max_size=100, flush_interval_sec=0.0)
        buf.tick()
        assert sent == [] and buf.stats()["flushes"] == 0


class TestRetryAndFailure:
    def test_flush_retries_on_transient_error(self):
        calls = {"n": 0}
        events: list[list[dict]] = []

        def flaky(events_):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("kafka down")
            events.append(events_)

        buf = BatchBuffer(flaky, max_size=10, flush_interval_sec=60.0, max_retries=3, backoff_base=0.01)
        for i in range(3):
            buf.add(_event(i))
        assert buf.flush() == 3  # retried and delivered
        assert calls["n"] == 2
        assert buf.stats()["failures"] == 0

    def test_persistent_failure_retains_buffer_and_raises(self):
        def always_fail(_events):
            raise ConnectionError("kafka down")

        buf = BatchBuffer(always_fail, max_size=10, flush_interval_sec=60.0, max_retries=2, backoff_base=0.01)
        for i in range(3):
            buf.add(_event(i))
        with pytest.raises(BatchFlushError):
            buf.flush()
        # nothing lost — the batch is still buffered for a later retry
        assert buf.pending == 3
        assert buf.stats()["failures"] == 1

    def test_events_never_silently_dropped(self):
        state = {"n": 0}

        def sender(events_):
            state["n"] += 1
            if state["n"] <= 2:
                raise RuntimeError("down")

        buf = BatchBuffer(sender, max_size=10, flush_interval_sec=60.0, max_retries=1, backoff_base=0.01)
        buf.add(_event(1))
        with pytest.raises(BatchFlushError):
            buf.flush()
        assert buf.pending == 1  # retained after failed flush
        assert buf.flush() == 1  # later flush delivers it
        assert buf.pending == 0


class TestRunBatch:
    def test_run_batch_delivers_all(self):
        sent: list[list[dict]] = []
        events = [_event(i) for i in range(5)]
        n = run_batch(events, sent.append, max_size=3)
        assert n == 5
        assert sum(len(b) for b in sent) == 5

    def test_run_batch_stats(self):
        sent: list[list[dict]] = []
        buf = BatchBuffer(sent.append, max_size=4)
        buf.add_many([_event(i) for i in range(4)])
        assert buf.stats()["sent"] == 4
        assert buf.stats()["flushes"] == 1