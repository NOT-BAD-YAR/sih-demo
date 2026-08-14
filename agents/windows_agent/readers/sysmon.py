"""Sysmon reader (wevtutil).

If Sysmon is installed, its operational channel logs process create (1),
network connect (3) and file create (11) events. We read the channel with the
same wevtutil mechanism as the security log; when the channel is missing the
reader reports unavailable and the agent keeps running without it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import AgentConfig
from ._winutil import parse_wevtutil_xml, run_capture
from .base import Reader

_WANTED = "(EventID=1 or EventID=3 or EventID=11)"


class SysmonReader(Reader):
    name = "sysmon"

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._last_query_end = datetime.now(timezone.utc) - timedelta(seconds=config.poll_interval_sec)

    def available(self) -> bool:
        return self._query() is not None

    def poll_once(self) -> list[dict]:
        text = self._query()
        if text is None:
            raise RuntimeError("wevtutil Sysmon query failed")
        raw_events = parse_wevtutil_xml(text)
        now = datetime.now(timezone.utc)
        start, self._last_query_end = self._last_query_end, now

        out: list[dict] = []
        for rec in raw_events:
            ts = _parse_ts(rec.get("ts"))
            if ts is None:
                continue
            if ts < start or ts > now:
                continue
            out.append(self._to_raw(rec, ts))
        return out

    def _query(self) -> str | None:
        query = f"*[System[{_WANTED}]]"
        cmd = [
            self.config.wevtutil_bin, "qe", self.config.sysmon_channel,
            "/q", query, "/rd:true", "/c:200", "/f:xml",
        ]
        return run_capture(cmd)

    @staticmethod
    def _to_raw(rec: dict, ts: datetime) -> dict:
        win_id = rec.get("event_id_win")
        raw = {
            "source": "sysmon",
            "event_id_win": win_id,
            "ts": ts.isoformat(),
            "user": rec.get("User", ""),
        }
        if win_id == 1:  # process create
            raw.update(
                image=rec.get("Image", ""),
                process_id=_int_or_none(rec.get("ProcessId")),
                command_line=rec.get("CommandLine", ""),
                integrity_level=rec.get("IntegrityLevel", ""),
            )
        elif win_id == 3:  # network connect
            raw.update(
                image=rec.get("Image", ""),
                process_id=_int_or_none(rec.get("ProcessId")),
                protocol=rec.get("Protocol", ""),
                destination_ip=rec.get("DestinationIp", ""),
                destination_port=_int_or_none(rec.get("DestinationPort")),
                destination_hostname=rec.get("DestinationHostname", ""),
            )
        elif win_id == 11:  # file create
            raw.update(
                image=rec.get("Image", ""),
                process_id=_int_or_none(rec.get("ProcessId")),
                target_filename=rec.get("TargetFilename", ""),
                file_size=_int_or_none(rec.get("Size")),
            )
        return raw


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None