"""Ground-truth records.

Used ONLY by evaluation (Phase 9) to compute Precision/Recall. Never read by
the live risk path. In Phase 3 these persist to the `ground_truth` table; here
they are in-memory records.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GroundTruthRecord:
    scenario: str
    entity_id: str
    start: str
    end: str
    related_event_ids: list[str] = field(default_factory=list)
    rule: str = ""
    expected_risk_band: str = ""

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "entity_id": self.entity_id,
            "start": self.start,
            "end": self.end,
            "related_event_ids": self.related_event_ids,
            "rule": self.rule,
            "expected_risk_band": self.expected_risk_band,
        }


_records: list[GroundTruthRecord] = []


def record(
    scenario: str,
    entity_id: str,
    start: str,
    end: str,
    related_event_ids: list[str] | None = None,
    rule: str = "",
    expected_risk_band: str = "",
) -> None:
    _records.append(
        GroundTruthRecord(
            scenario=scenario,
            entity_id=entity_id,
            start=start,
            end=end,
            related_event_ids=related_event_ids or [],
            rule=rule,
            expected_risk_band=expected_risk_band,
        )
    )


def all_records() -> list[GroundTruthRecord]:
    return list(_records)


def clear() -> None:
    _records.clear()