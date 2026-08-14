"""Windows Security log reader (wevtutil).

Queries the Security channel for logon / logon-failure / privilege events
(4624, 4625, 4672) from the last poll window and maps each to a raw dict the
normalizer understands. Uses the built-in `wevtutil` CLI so no pywin32 is
required; a real box simply needs the Security log readable (common for
standard users in the Administrators/Security groups).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import AgentConfig
from ._winutil import parse_wevtutil_xml, run_capture
from .base import Reader

_WANTED = "(EventID=4624 or EventID=4625 or EventID=4672)"


class SecurityLogReader(Reader):
    name = "security_log"

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._last_query_end = datetime.now(timezone.utc) - timedelta(seconds=config.poll_interval_sec)

    def available(self) -> bool:
        # probe: a single recent query must succeed (channel exists + readable)
        return self._query() is not None

    def poll_once(self) -> list[dict]:
        text = self._query()
        if text is None:
            raise RuntimeError("wevtutil Security query failed")
        raw_events = parse_wevtutil_xml(text)
        now = datetime.now(timezone.utc)
        start, self._last_query_end = self._last_query_end, now

        out: list[dict] = []
        for rec in raw_events:
            ts = _parse_ts(rec.get("ts"))
            if ts is None:
                continue
            if ts < start or ts > now:  # only events inside this poll window
                continue
            out.append(self._to_raw(rec, ts))
        return out

    def _query(self) -> str | None:
        query = f"*[System[{_WANTED}]]"
        cmd = [
            self.config.wevtutil_bin, "qe", self.config.security_log_channel,
            "/q", query, "/rd:true", "/c:200", "/f:xml",
        ]
        return run_capture(cmd)

    @staticmethod
    def _to_raw(rec: dict, ts: datetime) -> dict:
        user = rec.get("SubjectUserName") or rec.get("TargetUserName") or ""
        ip = rec.get("IpAddress", "")
        if ip in ("-", "::1"):
            ip = ""
        return {
            "source": "security_log",
            "event_id_win": rec.get("event_id_win"),
            "ts": ts.isoformat(),
            "user": user,
            "ip": ip,
            "logon_type": _int_or_none(rec.get("LogonType")),
            "process": rec.get("ProcessName", ""),
            "process_id": _int_or_none(rec.get("ProcessId")),
            "source_host": rec.get("WorkstationName", ""),
        }


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