"""Phase 8 — normalizer maps every reader source into the Common Event Schema."""

import pytest
from datetime import datetime, timezone

from agents.windows_agent.normalize import to_schema
from simulator.schema import ENTITY_TYPES, EVENT_TYPES, is_valid

pytestmark = pytest.mark.unit

HOST = "DESKTOP-TEST1"


def _ts(s: str = "2026-08-14T05:00:00Z") -> str:
    return s


class TestSecurityLogMapping:
    def test_4624_login_success(self):
        ev = to_schema(
            dict(source="security_log", event_id_win=4624, ts=_ts(), user="CORP\\alice", ip="10.0.0.5", logon_type=2),
            HOST,
        )
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "login"
        assert ev.outcome == "success"
        assert ev.user_id == "alice"  # domain\\user stripped
        assert ev.entity_type == "device"
        assert ev.entity_id == HOST
        assert ev.ip == "10.0.0.5"

    def test_4625_failure(self):
        ev = to_schema(dict(source="security_log", event_id_win=4625, ts=_ts(), user="CORP\\bob", ip="203.0.113.9"), HOST)
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "failure"
        assert ev.outcome == "failure"
        assert ev.user_id == "bob"

    def test_4672_privilege(self):
        ev = to_schema(dict(source="security_log", event_id_win=4672, ts=_ts(), user="CORP\\admin"), HOST)
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "privilege"

    def test_unmapped_security_event_id_dropped(self):
        ev = to_schema(dict(source="security_log", event_id_win=1102, ts=_ts(), user="x"), HOST)
        assert ev is None


class TestSysmonMapping:
    def test_event1_process_create(self):
        ev = to_schema(
            dict(source="sysmon", event_id_win=1, ts=_ts(),
                 user="CORP\\alice", image="C:\\Windows\\System32\\powershell.exe", process_id=1234, command_line="-enc"),
            HOST,
        )
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "process"
        assert ev.target_entity == "C:\\Windows\\System32\\powershell.exe"
        assert ev.raw_payload["process_id"] == 1234

    def test_event3_network_connect(self):
        ev = to_schema(
            dict(source="sysmon", event_id_win=3, ts=_ts(), user="CORP\\alice",
                 destination_ip="203.0.113.77", destination_port=443, protocol="tcp"),
            HOST,
        )
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "network_conn"
        assert ev.peer_entity == "203.0.113.77"
        assert ev.ip == "203.0.113.77"

    def test_event11_file_create(self):
        ev = to_schema(
            dict(source="sysmon", event_id_win=11, ts=_ts(), user="CORP\\alice",
                 target_filename="C:\\Users\\alice\\report.xlsx", file_size=1024),
            HOST,
        )
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "file_access"
        assert ev.file_path == "C:\\Users\\alice\\report.xlsx"
        assert ev.bytes_moved == 1024

    def test_unmapped_sysmon_event_id_dropped(self):
        ev = to_schema(dict(source="sysmon", event_id_win=13, ts=_ts(), user="x"), HOST)
        assert ev is None


class TestFileWatcherMapping:
    def test_created_file(self):
        ev = to_schema(
            dict(source="file_watcher", ts=_ts(), path="C:\\Users\\alice\\Downloads\\archive.zip", size=4096, action="created"),
            HOST,
        )
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "file_access"
        assert ev.file_path == "C:\\Users\\alice\\Downloads\\archive.zip"
        assert ev.bytes_moved == 4096

    def test_missing_path_dropped(self):
        assert to_schema(dict(source="file_watcher", ts=_ts(), path="", size=0, action="created"), HOST) is None


class TestUsbMapping:
    def test_inserted_usb(self):
        ev = to_schema(
            dict(source="usb", ts=_ts(), device="SanDisk USB", device_id="USBSTOR\\DISK&VEN_SANDISK", action="inserted"),
            HOST,
        )
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "usb"
        assert ev.target_entity == "USBSTOR\\DISK&VEN_SANDISK"
        assert ev.raw_payload["action"] == "inserted"

    def test_missing_device_id_dropped(self):
        assert to_schema(dict(source="usb", ts=_ts(), device_id="", action="inserted"), HOST) is None


class TestProcessMapping:
    def test_started_process(self):
        ev = to_schema(dict(source="process", ts=_ts(), process="powershell.exe", pid=9999, action="started"), HOST)
        assert ev is not None and is_valid(ev)
        assert ev.event_type == "process"
        assert ev.target_entity == "powershell.exe"
        assert ev.raw_payload["pid"] == 9999

    def test_missing_process_dropped(self):
        assert to_schema(dict(source="process", ts=_ts(), process="", pid=0, action="started"), HOST) is None


class TestRobustness:
    def test_unknown_source_dropped(self):
        assert to_schema(dict(source="registry", ts=_ts(), whatever=1), HOST) is None

    def test_bad_timestamp_dropped(self):
        ev = to_schema(dict(source="security_log", event_id_win=4624, ts="not-a-date", user="x"), HOST)
        assert ev is None

    def test_unix_epoch_timestamp_accepted(self):
        ev = to_schema(dict(source="usb", ts=1755190800, device="X", device_id="USBSTOR\\Y", action="inserted"), HOST)
        assert ev is not None and is_valid(ev)
        assert ev.ts == datetime(2025, 8, 14, 17, 0, tzinfo=timezone.utc)

    def test_all_mapped_events_are_schema_valid(self):
        cases = [
            dict(source="security_log", event_id_win=4624, ts=_ts(), user="CORP\\u", ip="1.2.3.4"),
            dict(source="sysmon", event_id_win=1, ts=_ts(), user="CORP\\u", image="C:\\bin.exe"),
            dict(source="file_watcher", ts=_ts(), path="C:\\a.csv", size=1, action="created"),
            dict(source="usb", ts=_ts(), device="d", device_id="ID1", action="removed"),
            dict(source="process", ts=_ts(), process="x.exe", pid=1, action="stopped"),
        ]
        for raw in cases:
            ev = to_schema(raw, HOST)
            assert ev is not None, raw
            assert is_valid(ev), (raw, ev)
            assert ev.entity_type in ENTITY_TYPES
            assert ev.event_type in EVENT_TYPES