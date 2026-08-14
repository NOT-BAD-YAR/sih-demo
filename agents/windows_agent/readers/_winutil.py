"""Shared helpers for Windows CLI readers.

Dependency-free collection: `wevtutil` (Event Log XML) and `powershell` / the
built-in tasklist CSV are all part of Windows. If a helper is missing or a
query fails, the caller marks its reader unavailable and moves on — no third
party DLLs are required, and pywin32/watchdog can be added later as
accelerators without changing the reader interface.
"""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from typing import Any


def run_capture(cmd: list[str], timeout: float = 20.0) -> str | None:
    """Run a command, return stdout text, or None on any failure."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _local(tag: str) -> str:
    """Strip any XML namespace so element tags compare by local name."""
    return tag.rsplit("}", 1)[-1]


def parse_wevtutil_xml(text: str) -> list[dict[str, Any]]:
    """Parse `wevtutil ... /f:xml` output into raw event dicts.

    Each dict carries the fields the normalize layer expects for the
    security_log / sysmon sources.
    """
    if not text:
        return []
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []

    events: list[dict[str, Any]] = []
    for event in root:
        if _local(event.tag) != "Event":
            continue
        rec = {"event_id_win": None, "ts": None, "user": "", "ip": "", "computer": ""}
        event_data: dict[str, str] = {}
        for section in event:
            tag = _local(section.tag)
            if tag == "System":
                _parse_system(section, rec)
            elif tag == "EventData":
                _parse_eventdata(section, event_data)
        if rec["event_id_win"] is not None:
            rec.update(event_data)
            events.append(rec)
    return events


def _parse_system(system: ET.Element, rec: dict[str, Any]) -> None:
    for field in system:
        tag = _local(field.tag)
        if tag == "EventID":
            rec["event_id_win"] = _int(field.text)
        elif tag == "TimeCreated":
            rec["ts"] = field.get("SystemTime")
        elif tag == "Computer":
            rec["computer"] = (field.text or "").strip()
        elif tag == "Provider":
            rec["provider"] = field.get("Name", "")


def _parse_eventdata(event_data: ET.Element, out: dict[str, str]) -> None:
    for data in event_data:
        if _local(data.tag) != "Data":
            continue
        name = data.get("Name")
        if not name:
            continue
        out[name] = (data.text or "").strip()


def _int(value: str | None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None