"""Shared test configuration.

Adds the repo root to sys.path so tests can import `analytics`, `streaming`,
etc. directly regardless of which directory pytest runs from.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))