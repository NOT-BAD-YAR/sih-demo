"""Novel-peer rule  -  canonical anomaly #5.

Triggers when an entity (typically a server) contacts a peer it has never
contacted before. The baseline's `known_peer_set` plus a per-peer frequency
map decide novelty; no ML is involved. First-ever peers score higher than
known-but-rare ones.
"""

from __future__ import annotations

from . import RuleResult, clamp01, not_triggered

RULE_NAME = "novel_peer"

SEVERITY_NEW = 0.8      # peer never seen at all
SEVERITY_RARE = 0.5     # peer known but extremely infrequent


def evaluate(
    ev: dict,
    known_peers: set[str] | None = None,
    peer_frequency: dict[str, int] | None = None,
    evidence: list[str] | None = None,
) -> RuleResult:
    """Detect contact with a never-before-seen peer.

    `known_peers` is the baseline allowed-set; `peer_frequency` maps peer  to 
    count in recent history (0/absent = first-ever).
    """
    evidence = evidence or []
    peer = (ev.get("peer_entity") or "").strip()
    if not peer:
        return not_triggered(RULE_NAME)

    known = known_peers or set()
    if peer in known:
        return not_triggered(RULE_NAME)

    freq = peer_frequency or {}
    count = freq.get(peer, 0)

    if count == 0:
        explanation = (
            f"{ev.get('entity_id', '?')} contacted novel peer {peer}  -  "
            f"first time seen in baseline history"
        )
        return RuleResult(RULE_NAME, True, SEVERITY_NEW, explanation, evidence)

    explanation = (
        f"{ev.get('entity_id', '?')} contacted peer {peer} outside the known "
        f"peer set (seen only {count}x recently)"
    )
    return RuleResult(RULE_NAME, True, clamp01(SEVERITY_RARE), explanation, evidence)