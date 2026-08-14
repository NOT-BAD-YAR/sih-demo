"""Source → Common Event Schema normalizer.

Every reader emits a *raw* record with source-specific fields. `to_schema`
maps those raw records into the single normalized Event shape that the rest of
the platform understands (simulator/schema.py). Unmappable / unparseable raw
records return None (dropped) rather than raising — the agent stays up.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from simulator.schema import ENTITY_TYPES, Event, build_event

VALID_SOURCES = ("security_log", "sysmon", "file_watcher", "usb", "process")

# Windows Security event IDs we care about
SEC_LOGIN = 4624
SEC_LOGON_FAILURE = 4625
SEC_PRIVILEGE = 4672

# Sysmon event IDs we care about
SYSMON_PROCESS_CREATE = 1
SYSMON_NETWORK_CONNECT = 3
SYSMON_FILE_CREATE = 11

_RE_UID = re.compile(r"(?:CORP|NT AUTHORITY|DESKTOP)[\\/]([^\\/]+)", re.IGNORECASE)


def _norm_user(user: str, fallback: str = "") -> str:
    """Strip a Windows DOMAIN\\user into the bare account name."""
    if not user:
        return fallback
    user = user.strip()
    m = _RE_UID.search(user)
    return m.group(1) if m else user


def _norm_ts(ts: Any) -> datetime | None:
    """Accept iso string, datetime, or unix epoch; always UTC-aware."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(ts, str):
        ts = ts.strip()
        if not ts:
            return None
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            try:
                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            except ValueError:
                return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _norm_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _host_for(hostname: str) -> str:
    return (hostname or "AGENT-01").upper()


def to_schema(raw: dict[str, Any], hostname: str = "") -> Event | None:
    """Map a reader's raw record into a Common Event Schema Event.

    Returns None when the record cannot be normalized (unknown source, bad
    timestamp, unmapped Windows event id) — the caller drops it and continues.
    """
    source = raw.get("source")
    if source not in VALID_SOURCES:
        return None
    ts = _norm_ts(raw.get("ts"))
    if ts is None:
        return None
    host = _host_for(hostname)
    user = _norm_user(str(raw.get("user", "")))

    if source == "security_log":
        return _security_to_schema(raw, ts, user, host)
    if source == "sysmon":
        return _sysmon_to_schema(raw, ts, user, host)
    if source == "file_watcher":
        return _file_to_schema(raw, ts, user, host)
    if source == "usb":
        return _usb_to_schema(raw, ts, user, host)
    return _process_to_schema(raw, ts, user, host)


# -- per-source mappers -------------------------------------------------------


def _security_to_schema(raw: dict[str, Any], ts: datetime, user: str, host: str) -> Event | None:
    win_id = _norm_int(raw.get("event_id_win"))
    ip = str(raw.get("ip", "")).strip()

    if win_id == SEC_LOGIN:
        event_type, outcome = "login", "success"
    elif win_id == SEC_LOGON_FAILURE:
        event_type, outcome = "failure", "failure"
    elif win_id == SEC_PRIVILEGE:
        event_type, outcome = "privilege", "success"
    else:
        return None

    target = str(raw.get("process", "") or "WinLogon")
    return build_event(
        entity_type="device",
        entity_id=host,
        user_id=user,
        event_type=event_type,
        actor=user or host,
        source_entity=host,
        target_entity=target,
        peer_entity=str(raw.get("source_host", "") or ""),
        ip=ip,
        ts=ts,
        outcome=outcome,
        sensitivity="internal",
        raw_payload={
            "source": "security_log",
            "event_id_win": win_id,
            "logon_type": _norm_int(raw.get("logon_type")),
            "process_id": _norm_int(raw.get("process_id")),
        },
    )


def _sysmon_to_schema(raw: dict[str, Any], ts: datetime, user: str, host: str) -> Event | None:
    win_id = _norm_int(raw.get("event_id_win"))
    ip = str(raw.get("destination_ip", "")).strip()

    if win_id == SYSMON_PROCESS_CREATE:
        event_type = "process"
        image = str(raw.get("image", "") or raw.get("process", "") or "")
        target = image
        raw_payload = {
            "source": "sysmon",
            "event_id_win": win_id,
            "process": image,
            "process_id": _norm_int(raw.get("process_id")),
            "command_line": str(raw.get("command_line", "")),
            "integrity_level": str(raw.get("integrity_level", "")),
        }
    elif win_id == SYSMON_NETWORK_CONNECT:
        event_type = "network_conn"
        target = str(raw.get("destination_hostname", "") or ip)
        raw_payload = {
            "source": "sysmon",
            "event_id_win": win_id,
            "process": str(raw.get("image", "")),
            "process_id": _norm_int(raw.get("process_id")),
            "protocol": str(raw.get("protocol", "")),
            "destination_port": _norm_int(raw.get("destination_port")),
        }
    elif win_id == SYSMON_FILE_CREATE:
        event_type = "file_access"
        path = str(raw.get("target_filename", "") or raw.get("file_path", "") or "")
        target = path
        raw_payload = {
            "source": "sysmon",
            "event_id_win": win_id,
            "process": str(raw.get("image", "")),
            "process_id": _norm_int(raw.get("process_id")),
            "file_size": _norm_int(raw.get("file_size")),
        }
    else:
        return None

    return build_event(
        entity_type="device",
        entity_id=host,
        user_id=user,
        event_type=event_type,
        actor=user or host,
        source_entity=host,
        target_entity=target,
        peer_entity=ip if win_id == SYSMON_NETWORK_CONNECT else "",
        ip=ip,
        ts=ts,
        outcome="success",
        sensitivity="internal",
        file_path=target if event_type == "file_access" else None,
        bytes_moved=_norm_int(raw.get("file_size", raw.get("bytes", 0))),
        raw_payload=raw_payload,
    )


def _file_to_schema(raw: dict[str, Any], ts: datetime, user: str, host: str) -> Event | None:
    path = str(raw.get("path", "")).strip()
    if not path:
        return None
    action = str(raw.get("action", "accessed"))
    return build_event(
        entity_type="device",
        entity_id=host,
        user_id=user,
        event_type="file_access",
        actor=user or host,
        source_entity=host,
        target_entity=path,
        peer_entity="",
        ip="",
        ts=ts,
        outcome="success",
        sensitivity="internal",
        file_path=path,
        bytes_moved=_norm_int(raw.get("size", raw.get("bytes", 0))),
        raw_payload={"source": "file_watcher", "action": action, "size": _norm_int(raw.get("size", 0))},
    )


def _usb_to_schema(raw: dict[str, Any], ts: datetime, user: str, host: str) -> Event | None:
    device_id = str(raw.get("device_id", "")).strip()
    if not device_id:
        return None
    action = str(raw.get("action", "inserted"))
    return build_event(
        entity_type="device",
        entity_id=host,
        user_id=user,
        event_type="usb",
        actor=user or host,
        source_entity=host,
        target_entity=device_id,
        peer_entity="",
        ip="",
        ts=ts,
        outcome="success",
        sensitivity="internal",
        raw_payload={"source": "usb", "action": action, "device": str(raw.get("device", ""))},
    )


def _process_to_schema(raw: dict[str, Any], ts: datetime, user: str, host: str) -> Event | None:
    proc = str(raw.get("process", "")).strip()
    if not proc:
        return None
    action = str(raw.get("action", "started"))
    return build_event(
        entity_type="device",
        entity_id=host,
        user_id=user,
        event_type="process",
        actor=user or host,
        source_entity=host,
        target_entity=proc,
        peer_entity="",
        ip="",
        ts=ts,
        outcome="success",
        sensitivity="internal",
        raw_payload={
            "source": "process",
            "action": action,
            "pid": _norm_int(raw.get("pid")),
            "process": proc,
        },
    )