"""ML anomaly detection — pure core (SPEC-ml-anomaly-detector #17).

Pure functions (no I/O, no model object):
- `load_replay_events(path)` — read a JSON/JSONL replay set.
- `build_windows(events, window)` — group events by entity and bin by event_time.
- `featurize(window, feature_spec)` — turn a window's feature dict into an ordered
  FeatureVector (missing → 0.0, extra → dropped, both logged).
- `score_to_severity(score, bands)` — config-backed score → Severity mapping.
- `finding_to_wazuh_alert(finding)` — map an AnomalyFinding to the existing WazuhAlert
  ingestion contract.

The runner (`backend.anomaly_detector`) is the only I/O seam; everything here is
fully unit-testable and never imports scikit-learn.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.domain.anomaly import (
    AnomalyFinding,
    EntityActivityWindow,
    FeatureVector,
    RawLogEvent,
    ScoreBands,
)
from backend.domain.incident import Severity, WazuhAgent, WazuhAlert, WazuhRule
from backend.infra.logging import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Severity ↔ level band (inverse of services/wazuh.level_to_severity)
# ---------------------------------------------------------------------------
# Used by finding_to_wazuh_alert to pick a representative Wazuh `level` per
# severity band so downstream intake.accept() re-derives the same Severity.
# The midpoint of each band avoids edge cases that would land in an adjacent band.
_SEVERITY_TO_LEVEL: dict[Severity, int] = {
    Severity.LOW: 2,
    Severity.MEDIUM: 5,
    Severity.HIGH: 9,
    Severity.CRITICAL: 13,
}


# ---------------------------------------------------------------------------
# Canonical behavioral features produced by build_windows.
# The offline trainer uses the same names via featurize(feature_spec), so the
# train/serve path is structurally identical (research R2).
# ---------------------------------------------------------------------------
_CANONICAL_FEATURE_NAMES = [
    "logon_count",
    "device_count",
    "file_count",
    "email_count",
    "http_count",
    "distinct_pc",
    "after_hours_count",
    "removable_copy_count",
    "external_email_count",
    "flagged_http_count",
]


def _event_type(fields: dict[str, Any]) -> str | None:
    """Return the event type if it is one of the known categories."""
    for key in ("type", "event_type", "event"):
        t = fields.get(key)
        if t is not None:
            return str(t).lower()
    return None


def _is_after_hours(event_time: datetime, fields: dict[str, Any]) -> bool:
    """Return True if the event occurred outside core hours."""
    explicit = fields.get("after_hours")
    if explicit is not None:
        return bool(explicit)
    return event_time.hour < 8 or event_time.hour >= 18


def _aggregate_features(events: list[RawLogEvent]) -> dict[str, float]:
    """Aggregate a list of raw events into the canonical feature dict."""
    counts: dict[str, int] = {name: 0 for name in _CANONICAL_FEATURE_NAMES}
    pcs: set[str] = set()

    for ev in events:
        fields = ev.fields
        etype = _event_type(fields)
        if etype == "logon":
            counts["logon_count"] += 1
        elif etype == "device":
            counts["device_count"] += 1
        elif etype == "file":
            counts["file_count"] += 1
            if fields.get("to_removable") or fields.get("removable"):
                counts["removable_copy_count"] += 1
        elif etype == "email":
            counts["email_count"] += 1
            if fields.get("external") or fields.get("to_external"):
                counts["external_email_count"] += 1
        elif etype == "http":
            counts["http_count"] += 1
            if fields.get("flagged") or fields.get("flagged_category"):
                counts["flagged_http_count"] += 1

        pc = fields.get("pc")
        if pc is not None:
            pcs.add(str(pc))

        if _is_after_hours(ev.event_time, fields):
            counts["after_hours_count"] += 1

    counts["distinct_pc"] = len(pcs)
    return {k: float(v) for k, v in counts.items()}


# ---------------------------------------------------------------------------
# Replay event loader
# ---------------------------------------------------------------------------


def load_replay_events(path: str | Path) -> list[RawLogEvent]:
    """Load a JSON or JSONL replay set; malformed entries are skipped (FR-011).

    - A `.jsonl` file is read line-by-line; each line must be a JSON object.
    - Other extensions are parsed as a single JSON list.
    - Records missing `event_time` or `entity_id` are skipped.
    """
    p = Path(path)
    if not p.exists():
        return []

    raw_records: list[dict[str, Any]] = []
    text = p.read_text()
    if p.suffix.lower() == ".jsonl":
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("anomaly_replay_malformed_line", line=line[:80])
                continue
            if isinstance(rec, dict):
                raw_records.append(rec)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"replay set is not valid JSON: {p}") from exc
        if not isinstance(data, list):
            raise ValueError(f"replay set root must be a list, got {type(data).__name__}")
        raw_records = data

    events: list[RawLogEvent] = []
    for rec in raw_records:
        if not isinstance(rec, dict):
            logger.warning("anomaly_replay_skip_non_object", type=type(rec).__name__)
            continue
        event_time_raw = rec.get("event_time")
        entity_id = rec.get("entity_id")
        if event_time_raw is None or entity_id is None:
            logger.warning("anomaly_replay_skip_missing_keys", keys=sorted(rec.keys()))
            continue
        try:
            events.append(
                RawLogEvent(
                    event_time=event_time_raw,  # type: ignore[arg-type]
                    entity_id=str(entity_id),
                    fields=rec.get("fields") or rec.get("data") or {},
                )
            )
        except ValueError as exc:
            logger.warning("anomaly_replay_skip_invalid", error=str(exc))
            continue

    return events


# ---------------------------------------------------------------------------
# Window builder
# ---------------------------------------------------------------------------


def build_windows(events: list[RawLogEvent], window: timedelta) -> list[EntityActivityWindow]:
    """Group events by entity and bin them into fixed event_time windows.

    Windows are aligned to the first event_time of each entity and advance by
    `window`. They are half-open: [start, start + window). Each window carries
    the canonical aggregated feature dict and the raw event count.

    Pure: no wall-clock, no I/O.
    """
    if window.total_seconds() <= 0:
        raise ValueError("window must be positive")

    by_entity: dict[str, list[RawLogEvent]] = defaultdict(list)
    for ev in events:
        by_entity[ev.entity_id].append(ev)

    windows: list[EntityActivityWindow] = []
    for entity_id, entity_events in by_entity.items():
        entity_events.sort(key=lambda e: e.event_time)
        start = entity_events[0].event_time
        end = start + window
        current_bucket: list[RawLogEvent] = []

        for ev in entity_events:
            if ev.event_time >= end:
                if current_bucket:
                    windows.append(
                        EntityActivityWindow(
                            entity_id=entity_id,
                            window_start=start,
                            window_end=end,
                            features=_aggregate_features(current_bucket),
                            raw_event_count=len(current_bucket),
                        )
                    )
                # Advance window until the event fits (skips empty buckets).
                while ev.event_time >= end:
                    start = end
                    end = start + window
                current_bucket = [ev]
            else:
                current_bucket.append(ev)

        if current_bucket:
            windows.append(
                EntityActivityWindow(
                    entity_id=entity_id,
                    window_start=start,
                    window_end=end,
                    features=_aggregate_features(current_bucket),
                    raw_event_count=len(current_bucket),
                )
            )

    windows.sort(key=lambda w: (w.entity_id, w.window_start))
    return windows


# ---------------------------------------------------------------------------
# Featurize (dense, ordered vector)
# ---------------------------------------------------------------------------


def featurize(window: EntityActivityWindow, feature_spec: list[str]) -> FeatureVector:
    """Turn a window's feature dict into an ordered FeatureVector.

    - Missing features → 0.0 (logged).
    - Extra features → dropped (logged).
    - The same function is used at train time and replay time (zero train/serve skew).
    """
    values: list[float] = []
    window_features = window.features
    missing = [name for name in feature_spec if name not in window_features]
    extra = [name for name in window_features if name not in feature_spec]

    if missing:
        logger.info(
            "anomaly_featurize_missing_features",
            entity_id=window.entity_id,
            features=missing,
        )
    if extra:
        logger.info(
            "anomaly_featurize_extra_features",
            entity_id=window.entity_id,
            features=extra,
        )

    for name in feature_spec:
        values.append(float(window_features.get(name, 0.0)))

    return FeatureVector(entity_id=window.entity_id, values=values)


# ---------------------------------------------------------------------------
# Score → severity + fire gate
# ---------------------------------------------------------------------------


def score_to_severity(score: float, bands: ScoreBands) -> Severity | None:
    """Map an anomaly score to a Severity using config-backed bands.

    Returns None when the score is below the fire_threshold (no alert).
    """
    if score < bands.fire_threshold:
        return None
    if score >= bands.band_critical:
        return Severity.CRITICAL
    if score >= bands.band_high:
        return Severity.HIGH
    if score >= bands.band_medium:
        return Severity.MEDIUM
    return Severity.LOW


def _top_features(window: EntityActivityWindow, n: int = 3) -> list[str]:
    """Return the n highest-valued feature names as evidence (FR-015)."""
    if not window.features:
        return []
    sorted_items = sorted(
        window.features.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    return [name for name, _ in sorted_items[:n]]


# ---------------------------------------------------------------------------
# AnomalyFinding -> WazuhAlert (emission contract)
# ---------------------------------------------------------------------------


def finding_to_wazuh_alert(finding: AnomalyFinding) -> WazuhAlert:
    """Map an AnomalyFinding to the existing WazuhAlert ingestion type.

    Reuses the shared severity→level inverse so intake.accept() re-derives the
    same Severity. The alert data carries the score + contributing features +
    entity_id as evidence (an anomaly score, not a rule identity).
    """
    level = _SEVERITY_TO_LEVEL[finding.severity]
    data: dict[str, Any] = {
        **finding.window.features,
        "entity_id": finding.entity_id,
        "score": round(finding.score, 6),
        "top_features": finding.top_features,
        "window_start": finding.window.window_start.isoformat(),
        "window_end": finding.window.window_end.isoformat(),
    }
    full_log = (
        f"anomaly: entity={finding.entity_id} "
        f"score={finding.score:.4f} "
        f"window={finding.window.window_start.isoformat()}/"
        f"{finding.window.window_end.isoformat()} "
        f"top_features={','.join(finding.top_features)}"
    )
    return WazuhAlert(
        rule=WazuhRule(
            id="anomaly-ueba",
            level=level,
            description=(
                f"Behavioral anomaly: {finding.entity_id} deviates from baseline "
                f"(score={finding.score:.4f})"
            ),
            groups=["ueba", "anomaly"],
        ),
        agent=WazuhAgent(name=finding.entity_id),
        data=data,
        full_log=full_log,
    )
