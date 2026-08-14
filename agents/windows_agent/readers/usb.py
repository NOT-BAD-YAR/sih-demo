"""USB / PnP reader (PowerShell WMI).

Enumerates USB storage devices with a built-in PowerShell WMI query and diffs
against the previous snapshot to emit `inserted` / `removed` raw records. When
PowerShell or WMI is unavailable the reader reports unavailable (fail-open) —
the rest of the agent keeps collecting.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from ..config import AgentConfig
from ._winutil import run_capture
from .base import Reader

_QUERY = (
    "Get-CimInstance Win32_PnPEntity | Where-Object { $_.DeviceID -like 'USBSTOR*' } | "
    "Select-Object Name, DeviceID | ConvertTo-Csv -NoTypeInformation"
)


class UsbReader(Reader):
    name = "usb"

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)
        self._snapshot: set[str] = set()

    def available(self) -> bool:
        return run_capture([self.config.powershell_bin, "-NoProfile", "-Command", _QUERY]) is not None

    def poll_once(self) -> list[dict]:
        text = run_capture([self.config.powershell_bin, "-NoProfile", "-Command", _QUERY])
        if text is None:
            raise RuntimeError("PowerShell USB query failed")
        current = _parse_csv(text)
        now = datetime.now(timezone.utc)

        records: list[dict] = []
        for device_id, name in current.items():
            if device_id not in self._snapshot:
                records.append(_record(device_id, name, "inserted", now))
        for device_id in self._snapshot - set(current):
            records.append(_record(device_id, "unknown", "removed", now))

        self._snapshot = set(current)
        return records


def _parse_csv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            device_id = (row.get("DeviceID") or "").strip()
            name = (row.get("Name") or "").strip()
            if device_id:
                out[device_id] = name
    except Exception:
        return {}
    return out


def _record(device_id: str, name: str, action: str, now: datetime) -> dict:
    return {
        "source": "usb",
        "ts": now.isoformat(),
        "device": name,
        "device_id": device_id,
        "action": action,
        "user": "",
        "hostname": "",
    }