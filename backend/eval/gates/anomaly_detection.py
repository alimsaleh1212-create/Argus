"""`anomaly_detection` eval gate (SPEC-ml-anomaly-detector #17).

Deterministic / provider-independent: loads the committed Isolation Forest
artifact via `SklearnAnomalyModel`, scores the labeled replay fixture, and
computes precision, recall, and false-positive rate.

Scoring (per `contracts/anomaly-eval.md`):
  - TP: a malicious-labeled window scores >= fire_threshold.
  - FP: a normal-labeled window scores >= fire_threshold.
  - FN: a malicious-labeled window scores < fire_threshold.
  - TN: a normal-labeled window does not fire.

Registered in the same change as the yaml declaration — orphan/stale mismatch
is a hard error (exit 2) per #13.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.domain.anomaly import ScoreBands, parse_window
from backend.domain.eval import GateKind, GateResult, GateSpec
from backend.eval.gates import GATE_REGISTRY
from backend.infra.anomaly_model import ModelArtifactError, SklearnAnomalyModel
from backend.services.anomaly import build_windows, featurize, load_replay_events

_MODEL_PATH = Path("backend/data/anomaly/model.joblib")
_REPLAY_PATH = Path("tests/fixtures/anomaly/replay/scenarios.jsonl")


def _load_labels() -> list[dict]:
    """Load labels from the JSONL fixture (one label per raw record)."""
    if not _REPLAY_PATH.exists():
        return []
    labels: list[dict] = []
    for line in _REPLAY_PATH.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            labels.append(rec)
    return labels


def _window_label(label_records: list[dict], entity_id: str, window_start: str) -> str | None:
    """Return the most common non-null label for records in a window.

    Windows are identified by (entity_id, window_start ISO day). If any record
    in the window is malicious, treat the window as malicious.
    """
    day = window_start[:10]
    labels: list[str] = []
    for rec in label_records:
        if rec.get("entity_id") != entity_id:
            continue
        et = rec.get("event_time", "")
        if not isinstance(et, str):
            continue
        if et[:10] != day:
            continue
        label = rec.get("label")
        if label:
            labels.append(str(label))
    if "malicious" in labels:
        return "malicious"
    if "normal" in labels:
        return "normal"
    return None


async def run_anomaly_detection(
    spec: GateSpec, provider: str | None = None
) -> GateResult:
    """Score the committed artifact over the labeled fixture and compute metrics."""
    if not _MODEL_PATH.exists() or not _REPLAY_PATH.exists():
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score={"precision": 0.0, "recall": 0.0, "false_positive_rate": 0.0},
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence="anomaly model or fixture missing",
        )

    try:
        model = SklearnAnomalyModel(_MODEL_PATH)
    except ModelArtifactError as exc:
        return GateResult(
            gate=spec.name,
            kind=spec.kind,
            provider=provider,
            score={"precision": 0.0, "recall": 0.0, "false_positive_rate": 0.0},
            threshold=spec.threshold,
            passed=None,
            blocking=spec.kind == GateKind.required,
            evidence=f"model artifact failed to load: {exc}",
        )

    threshold = spec.threshold
    fire_threshold = float(threshold.get("fire_threshold", 0.60))
    bands = ScoreBands(
        fire_threshold=fire_threshold,
        band_medium=fire_threshold,
        band_high=float(threshold.get("band_high", 0.75)),
        band_critical=float(threshold.get("band_critical", 0.90)),
    )
    window = parse_window(str(threshold.get("window", "1d")))

    events = load_replay_events(_REPLAY_PATH)
    windows = build_windows(events, window)
    labels = _load_labels()

    tp = fp = fn = tn = 0
    failures: list[str] = []

    for w in windows:
        vec = featurize(w, model.feature_spec)
        score = model.score([vec])[0]
        fired = score >= bands.fire_threshold
        label = _window_label(labels, w.entity_id, w.window_start.isoformat())

        if label == "malicious":
            if fired:
                tp += 1
            else:
                fn += 1
                failures.append(
                    f"FN {w.entity_id}@{w.window_start.date()} score={score:.4f}"
                )
        elif label == "normal":
            if fired:
                fp += 1
                failures.append(
                    f"FP {w.entity_id}@{w.window_start.date()} score={score:.4f}"
                )
            else:
                tn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0

    p_min = float(threshold.get("precision_min", 0.80))
    r_min = float(threshold.get("recall_min", 0.80))
    max_fpr = float(threshold.get("max_false_positive_rate", 0.10))
    passed = precision >= p_min and recall >= r_min and fpr <= max_fpr

    evidence = f"tp={tp} fp={fp} fn={fn} tn={tn} precision={precision:.2f} recall={recall:.2f} fpr={fpr:.2f}"
    if failures:
        evidence += "; " + "; ".join(failures)

    return GateResult(
        gate=spec.name,
        kind=spec.kind,
        provider=provider,
        score={"precision": precision, "recall": recall, "false_positive_rate": fpr},
        threshold=threshold,
        passed=passed,
        blocking=spec.kind == GateKind.required,
        evidence=evidence,
    )


GATE_REGISTRY["anomaly_detection"] = run_anomaly_detection
