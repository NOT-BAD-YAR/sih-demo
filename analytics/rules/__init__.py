"""Rule Engine  -  4.3 deterministic detectors for the 5 canonical cases.

Every rule is a pure function returning a `RuleResult`. A `RuleResult` carries
an explainable `explanation` sentence (the dashboard's "why flagged" card), a
0 - 1 `severity` anomaly contribution for the risk engine (4.6), and the
`evidence` event ids that triggered it.

The 5 canonical cases (plan.md  section 6.1):
  1. volume_spike       -  current volume  >>  baseline mean
  2. impossible_travel  -  two logins faster than physically possible
  3. out_of_scope       -  access to a resource owned by another department
  4. dormant            -  an idle account wakes up outside its active window
  5. novel_peer         -  a server contacts a never-before-seen peer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import types


def clamp01(value: float) -> float:
    """Clamp a numeric anomaly contribution into 0 - 1."""
    return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class RuleResult:
    """Outcome of one rule evaluation."""

    rule: str
    triggered: bool
    severity: float  # 0-1 anomaly contribution
    explanation: str  # human-readable "why flagged" sentence
    evidence: list[str] = field(default_factory=list)


def not_triggered(rule: str) -> RuleResult:
    """A rule that did not fire carries zero anomaly contribution."""
    return RuleResult(rule=rule, triggered=False, severity=0.0, explanation="")


REGISTRY: dict[str, "types.ModuleType"] = {}


def _register(module: "types.ModuleType") -> None:
    REGISTRY[module.RULE_NAME] = module


from . import volume_spike  # noqa: E402
from . import impossible_travel  # noqa: E402
from . import out_of_scope  # noqa: E402
from . import dormant  # noqa: E402
from . import novel_peer  # noqa: E402

for _module in (volume_spike, impossible_travel, out_of_scope, dormant, novel_peer):
    _register(_module)


def run_rule(name: str, **kwargs) -> RuleResult:
    """Dispatch to a registered rule by name."""
    module = REGISTRY.get(name)
    if module is None:
        raise KeyError(f"unknown rule: {name}")
    return module.evaluate(**kwargs)


def rule_names() -> list[str]:
    return sorted(REGISTRY)


__all__ = [
    "RuleResult",
    "clamp01",
    "REGISTRY",
    "run_rule",
    "rule_names",
    "not_triggered",
]