"""Pipeline Runner — 4.8 orchestrator wiring every analytics module together.

One consumed event in, alerts/incidents out:

    on_event(payload)
      validate -> resolve_user -> accumulate window
      (bucket rollover) -> close window -> build/select baseline (cold start)
        -> rules evaluate -> ml score -> context vector -> risk join
        -> correlation -> persist alerts/incidents

The runner is deliberately Kafka-optional: `on_event()` accepts any wire
payload dict, so unit/integration tests drive it directly. `run()` binds the
same handler to an `EngineConsumer`. `cron()` performs the daily rolling
retrain (baselines + ML models) against the store.

Design notes:
  * windows are closed on hour-bucket rollover (and `flush()` at shutdown);
  * a closed window is scored ONLY if at least one rule fires, or the ML
    signal is strong (>= ML_ONLY_THRESHOLD) — weak/no-signal windows stay
    silent so normal traffic never spams alerts;
  * cold-start entities (no baseline) are scored with LOW confidence (gentle);
  * every triggered rule becomes one alert (real DB id when a store is bound);
    alerts fold into incidents through `cluster_for_entity`.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .processor import validate, resolve_user
from .features import accumulate, finalize, hour_bucket, FeatureWindow
from .baseline import build_individual, build_peer_group, build_global, select_level, rolling_retrain
from .ml import score as ml_score, retrain_schedule, clear_models
from .context import build as ctx_build
from .risk import compute, fuse, impact as impact_fn
from .correlation import ScoredEvent, Incident, cluster_for_entity
from . import rules

ML_ONLY_THRESHOLD = 0.5   # a window with no rule firing still alerts on strong ML signal


def _dept_of(org, entity_ref: str) -> str | None:
    if org is None:
        return None
    for e in org.employees:
        if e.emp_id == entity_ref:
            return e.department
    for s in org.servers:
        if s.server_id == entity_ref:
            return s.department
    return None


def _actor_of(org, entity_ref: str):
    if org is None:
        return None
    for e in org.employees:
        if e.emp_id == entity_ref:
            return e
    for s in org.servers:
        if s.server_id == entity_ref:
            return s
    return None


def _peer_set_of(org, entity_ref: str) -> set:
    if org is None:
        return set()
    for s in org.servers:
        if s.server_id == entity_ref:
            return set(s.peers or ())
    return set()


def _staleness_days(entity_ref: str, closed: list[dict], org) -> float:
    """Days since the entity's last closed window (dormancy probe).

    New/unknown entities (e.g. a first-seen THREAT-DEVICE) are NOT dormant:
    they score 0. Registered-but-silent employees (dormant=True in the org)
    score very high so the dormant rule can fire.
    """
    last = None
    for w in closed:
        raw = w.get("window_start")
        parsed = datetime.fromisoformat(raw) if isinstance(raw, str) else raw
        if parsed and (last is None or parsed > last):
            last = parsed
    if last is not None:
        return max(0.0, (datetime.now(timezone.utc) - last).total_seconds() / 86400.0)
    if org is not None:
        for e in org.employees:
            if e.emp_id == entity_ref:
                return float("inf") if e.dormant else 0.0
    return 0.0


class AnalyticsRunner:
    """Orchestrates event -> window -> baseline -> rules+ml -> risk -> incident."""

    def __init__(self, consumer=None, store=None, org=None, history: dict | None = None):
        self.consumer = consumer
        self.store = store          # psycopg2 connection (or None for pure tests)
        self.org = org              # Organization (peer groups, owners, roles)
        self._history = dict(history or {})  # entity_ref -> past closed windows (baseline)
        self._windows: dict[str, FeatureWindow] = {}
        self._events: dict[tuple[str, str], list] = {}
        self._closed: dict[str, list[dict]] = defaultdict(list)
        self._open_incidents: list[Incident] = []
        self.stats = {
            "events": 0, "dropped": 0, "windows_closed": 0,
            "alerts": 0, "incidents": 0, "escalations": 0,
        }

    # -- DAO helpers (no-op when no store is bound) ---------------------------
    def _persist(self, fn, *args):
        if self.store is None:
            return None
        return fn(self.store, *args)

    # -- ingest ---------------------------------------------------------------
    def on_event(self, payload: dict) -> Any:
        """Ingest one wire payload; returns the NormalizedEvent or None (dropped)."""
        ev = validate(payload)
        if ev is None:
            self.stats["dropped"] += 1
            return None
        self.stats["events"] += 1
        resolve_user(ev)  # keep the human-actor resolution in the hot path (LLD 4.8)

        open_win = self._windows.get(ev.entity_id)
        if open_win is not None and hour_bucket(open_win.window_start) != hour_bucket(ev.ts):
            self._close_window(ev.entity_id)

        self._windows[ev.entity_id] = accumulate(self._windows.get(ev.entity_id), ev)
        self._events.setdefault((ev.entity_id, hour_bucket(ev.ts).isoformat()), []).append(ev)
        return ev

    def flush(self) -> int:
        """Close every open window (shutdown / test boundary). Returns count."""
        count = 0
        for entity_ref in list(self._windows):
            self._close_window(entity_ref)
            count += 1
        return count

    def run(self, max_messages: Optional[int] = None) -> int:
        """Drive the bound EngineConsumer (handler must be `self.on_event`)."""
        if self.consumer is None:
            raise RuntimeError(
                "AnalyticsRunner has no consumer bound; construct with "
                "consumer=EngineConsumer(..., handler=runner.on_event)"
            )
        return self.consumer.run(max_messages)

    # -- window lifecycle -----------------------------------------------------
    def _close_window(self, entity_ref: str) -> None:
        open_win = self._windows.pop(entity_ref)
        bucket_key = hour_bucket(open_win.window_start).isoformat()
        events = self._events.pop((entity_ref, bucket_key), [])
        win = finalize(open_win)
        self._closed[entity_ref].append(win)
        self._persist_upsert_window(entity_ref, open_win.window_start, win)
        self.stats["windows_closed"] += 1
        self._score_window(entity_ref, win, events)

    def _persist_upsert_window(self, entity_ref, window_ts, win) -> None:
        if self.store is not None:
            try:
                from db.dao import upsert_window

                upsert_window(self.store, entity_ref, window_ts, win)
            except Exception:
                pass

    # -- detection pipeline ---------------------------------------------------
    def _select_baseline(self, entity_ref: str, past_windows: list[dict] | None = None):
        """Choose the scoring baseline: (level, row) or (None, None) cold start.

        `past_windows` is the pre-anomaly history (never the just-scored window).
        Falls back to the store when no in-memory history was provided.
        """
        if not past_windows:
            past_windows = self._history.get(entity_ref) or self._get_stored_windows(entity_ref)
        individual = build_individual(entity_ref, past_windows) if past_windows else None
        profiles = {"individual": individual}
        for level in ("peer_group", "global"):
            profiles[level] = self._get_stored_profile(entity_ref, level)
        try:
            return select_level(entity_ref, profiles)
        except ValueError:
            return None, None

    def _get_stored_windows(self, entity_ref: str) -> list[dict]:
        if self.store is None:
            return []
        try:
            from db.dao import get_windows

            return [r["vector"] for r in get_windows(self.store, entity_ref)]
        except Exception:
            return []

    def _get_stored_profile(self, entity_ref: str, level: str) -> dict | None:
        if self.store is None:
            return None
        try:
            from db.dao import get_profile

            return get_profile(self.store, entity_ref, level)
        except Exception:
            return None

    def _score_window(self, entity_ref: str, win: dict, events: list) -> None:
        level, profile_row = self._select_baseline(entity_ref, self._history.get(entity_ref) or [])

        dept = _dept_of(self.org, entity_ref)
        ml = self._ml_signal(entity_ref, win, level, dept)

        results = self._evaluate_rules(entity_ref, win, events, profile_row, level, dept)

        scored: list[ScoredEvent] = []
        for rule_result, ev in results:
            se = self._compose_risk(entity_ref, ev, rule_result, ml, profile_row)
            if se is not None:
                scored.append(se)

        # strong ML signal alone still surfaces (ML-only detection path)
        if not scored and ml >= ML_ONLY_THRESHOLD and events:
            se = self._compose_risk(entity_ref, events[0], None, ml, profile_row)
            if se is not None:
                scored.append(se)

        if not scored:
            return

        incident = cluster_for_entity(entity_ref, scored, self._open_incidents)
        if incident is None:
            return
        if incident not in self._open_incidents:
            self._open_incidents.append(incident)
            self.stats["incidents"] += 1
            self._persist_incident(incident)
        else:
            self.stats["escalations"] += 1
            self._persist_incident(incident, update=True)

    def _ml_signal(self, entity_ref: str, win: dict, level: str | None, dept: str | None) -> float:
        fallback_keys: list[str] = []
        if dept:
            fallback_keys.append(f"peer_group:{dept}")
        fallback_keys.append("global:__global__")
        return ml_score("individual", entity_ref, win, fallback_keys=fallback_keys)

    def _evaluate_rules(self, entity_ref: str, win: dict, events: list,
                        profile_row: dict | None, level: str | None, dept: str | None) -> list:
        """Run the five canonical rules; returns [(RuleResult, event)] triggered."""
        out: list = []

        # volume_spike — window vs individual/peer baselines
        peer_row = profile_row if level == "peer_group" else None
        ind_row = profile_row if level == "individual" else None
        if events:
            r = rules.run_rule("volume_spike", window=win,
                               profile_individual=ind_row, profile_peer_group=peer_row)
            if r.triggered:
                out.append((r, events[0]))

        # impossible_travel — consecutive logins in the window
        logins = sorted(((e.geo, e.ts) for e in events if e.event_type == "login"), key=lambda p: p[1])
        if len(logins) >= 2:
            r = rules.run_rule("impossible_travel", login_pairs=logins)
            if r.triggered:
                out.append((r, _event_by_ts(events, logins[-1][1])))

        # dormant — evaluated once per window (first event)
        if events:
            staleness = _staleness_days(entity_ref, self._closed[entity_ref], self.org)
            r = rules.run_rule("dormant", ev=events[0].__dict__,
                               active_window=(profile_row or {}).get("active_window"),
                               staleness_days=staleness)
            if r.triggered:
                out.append((r, events[0]))

        # out_of_scope + novel_peer — per-event rules
        for ev in events:
            if ev.target_entity and self.org is not None:
                r = rules.run_rule("out_of_scope", ev=ev.__dict__,
                                   user_dept=dept, resource_owner=self.org.resource_owner)
                if r.triggered:
                    out.append((r, ev))
            if ev.peer_entity:
                r = rules.run_rule("novel_peer", ev=ev.__dict__,
                                   known_peers=_peer_set_of(self.org, ev.entity_id), peer_frequency={})
                if r.triggered:
                    out.append((r, ev))
        return out

    def _compose_risk(self, entity_ref: str, ev, rule_result, ml: float, profile_row: dict | None):
        actor = _actor_of(self.org, entity_ref)
        ctx = ctx_build(ev, profile_row, actor, self.org.resource_owner if self.org else None)
        severity = rule_result.severity if rule_result is not None else 0.0
        risk = compute(
            fuse([severity], ml),
            impact_fn(ctx.target_sensitivity, ctx.role_factor, ctx.dept_factor),
            ctx.baseline_confidence,
            rule_bonus=severity * 0.1,
            components={"rules": [severity], "ml": ml},
        )
        se = ScoredEvent(
            event_id=ev.event_id,
            entity_ref=entity_ref,
            ts=ev.ts,
            risk=risk.risk_100,
            severity=severity,
            chain=ev.chain,
        )
        # persist the alert, wire its real id into correlation evidence
        if self.store is not None:
            try:
                from db.dao import insert_alert

                se.alert_id = str(insert_alert(self.store, entity_ref, risk.band,
                                               int(round(risk.risk_100)), [ev.event_id]))
                self.stats["alerts"] += 1
            except Exception:
                se.alert_id = f"ALERT-{ev.event_id}"
        return se

    def _persist_incident(self, incident: Incident, update: bool = False) -> None:
        if self.store is None:
            return
        try:
            from db.dao import insert_incident, update_incident

            if update and incident.id is not None:
                update_incident(self.store, {**incident.row(), "id": incident.id})
            elif not update and incident.id is None:
                incident.id = insert_incident(self.store, incident.row())
        except Exception:
            pass


def _event_by_ts(events: list, ts: datetime):
    for ev in events:
        if ev.ts == ts:
            return ev
    return events[0] if events else None


def cron(store, org, entity_refs, *, last_n_days: int = 30) -> dict:
    """Daily rolling retrain: individual baselines + peer groups + global + ML.

    Mirrors LLD 4.8 `cron()`: rebuild every entity's baseline from the last
    `last_n_days` of persisted windows, rebuild peer-group/global aggregates,
    then retrain every cached ML model key. Returns a summary dict.
    """
    from db.dao import get_windows, upsert_profile

    individual_rows: dict[str, dict] = {}
    for ref in entity_refs:
        windows = [r["vector"] for r in get_windows(store, ref)]
        if not windows:
            continue
        row = rolling_retrain(ref, windows, last_n_days)
        upsert_profile(store, {**row, "entity_ref": ref, "level": "individual"})
        individual_rows[ref] = row

    by_dept: dict[str, list[str]] = defaultdict(list)
    for ref in entity_refs:
        dept = _dept_of(org, ref)
        if dept:
            by_dept[dept].append(ref)

    peer_groups = 0
    for dept, refs in by_dept.items():
        members = [individual_rows[r] for r in refs if r in individual_rows]
        if not members:
            continue
        group_row = build_peer_group(dept, members)
        for ref in refs:
            upsert_profile(store, {**group_row, "entity_ref": ref, "level": "peer_group"})
        peer_groups += 1

    if individual_rows:
        global_row = build_global(list(individual_rows.values()))
        for ref in entity_refs:
            upsert_profile(store, {**global_row, "entity_ref": ref, "level": "global"})

    history = {
        f"individual:{ref}": [r["vector"] for r in get_windows(store, ref)]
        for ref in entity_refs
    }
    retrained = retrain_schedule(history)

    return {
        "individuals": len(individual_rows),
        "peer_groups": peer_groups,
        "global": bool(individual_rows),
        "ml_retrained": retrained,
    }


__all__ = [
    "AnalyticsRunner",
    "cron",
    "ML_ONLY_THRESHOLD",
]
