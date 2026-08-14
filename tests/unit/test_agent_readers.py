"""Phase 8 — reader supervision (fail-open), parsing helpers, and real-CLI coverage."""

import pytest

from agents.windows_agent.config import AgentConfig
from agents.windows_agent.readers import build_readers
from agents.windows_agent.readers.base import Reader, ReaderRunner
from agents.windows_agent.readers._winutil import parse_wevtutil_xml
from agents.windows_agent.readers.file_watcher import FileWatcherReader, _record
from agents.windows_agent.readers.process import _parse_csv as parse_tasklist
from agents.windows_agent.readers.usb import _parse_csv as parse_pnp_csv

pytestmark = pytest.mark.unit

CFG = AgentConfig(enabled_readers=["security_log", "file_watcher"], watch_dirs=["C:\\Users"])


class _FlakyReader(Reader):
    name = "flaky"

    def __init__(self, config, fail_first: int = 0):
        super().__init__(config)
        self.calls = 0
        self.fail_first = fail_first

    def poll_once(self):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise RuntimeError("boom")
        return [{"source": "file_watcher", "ts": "2026-08-14T05:00:00Z", "path": "C:\\a.txt", "size": 1}]


class _GoodReader(Reader):
    name = "good"

    def poll_once(self):
        return [{"source": "file_watcher", "ts": "2026-08-14T05:00:00Z", "path": "C:\\b.txt", "size": 2}]


class _UnavailableReader(Reader):
    name = "unavailable"

    def available(self):
        return False

    def poll_once(self):
        raise AssertionError("should never be polled")


class TestReaderDisableOnError:
    def test_reader_disabled_after_max_consecutive_errors(self):
        sink: list[dict] = []
        runner = ReaderRunner(
            [_FlakyReader(CFG, fail_first=10)], sink, max_errors=3
        )
        for _ in range(4):
            runner.poll_all()
        status = runner.status()["flaky"]
        assert status["enabled"] is False
        assert status["errors"] == 3
        assert "disabled" in status["disabled_reason"].lower()
        assert sink == []  # nothing was emitted while failing

    def test_recovered_reader_keeps_working(self):
        sink: list[dict] = []
        runner = ReaderRunner([_FlakyReader(CFG, fail_first=2)], sink.append, max_errors=5)
        for _ in range(4):
            runner.poll_all()
        status = runner.status()["flaky"]
        assert status["enabled"] is True
        assert status["errors"] == 0  # success reset the counter
        assert len(sink) == 2  # two successful polls emitted records

    def test_one_bad_reader_does_not_block_others(self):
        sink: list[dict] = []
        runner = ReaderRunner(
            [_FlakyReader(CFG, fail_first=100), _GoodReader(CFG)], sink.append, max_errors=2
        )
        for _ in range(3):
            runner.poll_all()
        assert runner.status()["good"]["enabled"] is True
        assert len([e for e in sink if e["path"] == "C:\\b.txt"]) == 3
        assert runner.status()["flaky"]["enabled"] is False

    def test_enabled_count(self):
        runner = ReaderRunner([_GoodReader(CFG), _UnavailableReader(CFG)], sink=lambda _r: None)
        assert runner.enabled_count() == 1  # unavailable reader excluded at startup

    def test_unavailable_reader_never_polled(self):
        sink: list[dict] = []
        runner = ReaderRunner([_UnavailableReader(CFG)], sink)
        runner.poll_all()  # must not raise
        assert runner.status()["unavailable"]["enabled"] is False

    def test_start_skips_unavailable_and_runs_enabled(self):
        runner = ReaderRunner([_GoodReader(CFG), _UnavailableReader(CFG)], sink=lambda _r: None)
        runner.start()
        try:
            assert runner.enabled_count() == 1
            assert any(t.name == "reader-good" for t in runner._threads)
            assert not any(t.name == "reader-unavailable" for t in runner._threads)
        finally:
            runner.stop()

    def test_stop_is_idempotent(self):
        runner = ReaderRunner([_GoodReader(CFG)], sink=lambda _r: None)
        runner.start()
        runner.stop()
        runner.stop()  # second stop must be safe


class TestWevtutilParsing:
    XML = """<?xml version="1.0"?>
<Events>
  <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
    <System>
      <Provider Name="Microsoft-Windows-Security-Auditing" Guid="{abc}"/>
      <EventID>4624</EventID>
      <TimeCreated SystemTime="2026-08-14T05:00:00.000Z"/>
      <Computer>HOST01</Computer>
    </System>
    <EventData>
      <Data Name="SubjectUserName">alice</Data>
      <Data Name="IpAddress">10.0.0.5</Data>
      <Data Name="LogonType">2</Data>
      <Data Name="ProcessName">C:\\Windows\\lsass.exe</Data>
      <Data Name="ProcessId">1234</Data>
      <Data Name="WorkstationName">LAP-01</Data>
    </EventData>
  </Event>
  <Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event">
    <System>
      <Provider Name="Microsoft-Windows-Security-Auditing"/>
      <EventID>4625</EventID>
      <TimeCreated SystemTime="2026-08-14T05:00:01.000Z"/>
      <Computer>HOST01</Computer>
    </System>
    <EventData>
      <Data Name="SubjectUserName">bob</Data>
      <Data Name="IpAddress">203.0.113.9</Data>
    </EventData>
  </Event>
</Events>
"""

    def test_parses_multiple_events(self):
        events = parse_wevtutil_xml(self.XML)
        assert len(events) == 2
        assert events[0]["event_id_win"] == 4624
        assert events[1]["event_id_win"] == 4625

    def test_eventdata_extracted(self):
        events = parse_wevtutil_xml(self.XML)
        assert events[0]["SubjectUserName"] == "alice"
        assert events[0]["IpAddress"] == "10.0.0.5"
        assert events[0]["LogonType"] == "2"

    def test_garbage_returns_empty(self):
        assert parse_wevtutil_xml("<not-xml") == []
        assert parse_wevtutil_xml("") == []


class TestCsvParsers:
    def test_tasklist_csv(self):
        text = '"System Idle Process","0","Services","0","8 K"\r\n"powershell.exe","9999","Console","1","80,000 K"\r\n'
        procs = parse_tasklist(text)
        assert procs == {0: "System Idle Process", 9999: "powershell.exe"}

    def test_pnp_csv(self):
        text = '"Name","DeviceID"\r\n"SanDisk USB 3.0","USBSTOR\\DISK&VEN_SANDISK&PROD_ULTRA"\r\n'
        devices = parse_pnp_csv(text)
        assert devices == {"USBSTOR\\DISK&VEN_SANDISK&PROD_ULTRA": "SanDisk USB 3.0"}


class TestFileWatcherDiff:
    def test_created_modified_deleted(self, tmp_path):
        cfg = AgentConfig(enabled_readers=["file_watcher"], watch_dirs=[str(tmp_path)], file_extension_filter=[".txt"])
        reader = FileWatcherReader(cfg)
        target = tmp_path / "a.txt"
        target.write_text("hello")
        created = reader.poll_once()
        assert [r["action"] for r in created] == ["created"]
        assert created[0]["path"] == str(target)

        target.write_text("hello world")  # modified (size/mtime change)
        modified = reader.poll_once()
        assert [r["action"] for r in modified] == ["modified"]

        target.unlink()
        deleted = reader.poll_once()
        assert [r["action"] for r in deleted] == ["deleted"]

    def test_extension_filter_ignored(self, tmp_path):
        cfg = AgentConfig(enabled_readers=["file_watcher"], watch_dirs=[str(tmp_path)], file_extension_filter=[".xlsx"])
        (tmp_path / "notes.txt").write_text("n")
        reader = FileWatcherReader(cfg)
        assert reader.poll_once() == []  # .txt filtered out

    def test_unavailable_when_no_watch_dir(self, tmp_path):
        cfg = AgentConfig(enabled_readers=["file_watcher"], watch_dirs=[str(tmp_path / "missing")])
        assert FileWatcherReader(cfg).available() is False

    def test_record_shape(self):
        rec = _record("C:\\a.txt", "created", 10, 0.0, __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc))
        assert rec["source"] == "file_watcher"
        assert rec["action"] == "created" and rec["size"] == 10


class TestBuildReaders:
    def test_only_enabled_readers_instantiated(self):
        cfg = AgentConfig(enabled_readers=["security_log", "usb"])
        readers = build_readers(cfg)
        names = {r.name for r in readers}
        assert names == {"security_log", "usb"}

    def test_all_readers_by_default(self):
        readers = build_readers(AgentConfig())
        assert {r.name for r in readers} == {"security_log", "sysmon", "file_watcher", "usb", "process"}

    def test_each_reader_subclasses_base(self):
        for reader in build_readers(AgentConfig()):
            assert isinstance(reader, Reader)
            assert callable(reader.available)
            assert callable(reader.poll_once)