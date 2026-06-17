# Contract: `anomaly_detection` Eval Gate

**Owner**: `017-ml-anomaly-detector`. **Harness**: `backend/eval/` (#13). **Kind**: deterministic,
provider-independent (the detector has no LLM). **Declared in `config/eval_thresholds.yaml` AND registered
in `GATE_REGISTRY` AND imported in `backend/eval/__main__.py` in the same change** — an orphan declaration,
stale runner, or unimported module is a hard error (exit 2) per #13.

## Inputs

- **Model artifact**: the committed `backend/data/anomaly/model.joblib`, loaded via the real
  `SklearnAnomalyModel` (this is the one eval-job place that imports `scikit-learn`).
- **Labeled fixture**: `tests/fixtures/anomaly/replay/scenarios.jsonl` — replayed log records that
  aggregate into per-entity windows, each window carrying a ground-truth `label`
  (`malicious` = scenario-anomalous user-window / `normal`). A downsampled, hand-checkable slice of CERT.

## Scoring

Build windows (`services/anomaly.build_windows`), featurize, score with the committed model, apply
`fire_threshold`, then compare fired windows to labels:

- **TP**: a `malicious`-labeled window scores `>= fire_threshold` (fires).
- **FP**: a `normal`-labeled window scores `>= fire_threshold` (fires).
- **FN**: a `malicious`-labeled window scores `< fire_threshold` (no fire).
- **TN**: a `normal`-labeled window does not fire.

```
precision           = TP / (TP + FP)
recall              = TP / (TP + FN)
false_positive_rate = FP / (FP + TN)
```

## Thresholds (committed in `eval_thresholds.yaml`)

```yaml
anomaly_detection:
  description: >
    ML anomaly-detection precision/recall gate (SPEC-ml-anomaly-detector #17). Deterministic /
    provider-independent. Scores the committed Isolation Forest artifact over a labeled held-out
    set of per-entity windows (CERT-derived). Blocking: the gate scores the saved artifact, so it
    is deterministic with no runtime variance. Extends the suite; does not duplicate existing gates.
  required: true
  threshold:
    precision_min: 0.80
    recall_min: 0.80
    max_false_positive_rate: 0.10
```

(Seed values informed by published ~0.97 / <0.1-FP hybrids — **a target, not a commitment**; tighten once
the fixture is finalized. All three must hold to pass — SC-002/SC-003.)

## Registration

`backend/eval/gates/anomaly_detection.py` defines `async def run_anomaly_detection(spec, provider) ->
GateResult` and registers it: `GATE_REGISTRY["anomaly_detection"] = run_anomaly_detection`. It is imported
in `backend/eval/__main__._run` alongside the other gate modules. `validate_registry()` then sees
`anomaly_detection` declared (yaml) ⇔ registered (code). Score is `{"precision": p, "recall": r,
"false_positive_rate": fpr}`; `passed` iff `p >= precision_min and r >= recall_min and fpr <=
max_false_positive_rate`. Missing artifact/fixture → `passed=None` with evidence (mirrors #14's gate).

## Reproducibility (SC-008)

The gate **never retrains** — it scores the committed artifact against the committed fixture, so the score
is bit-stable across CI runs. Training reproducibility (regenerating the artifact from CERT) is a separate
offline concern handled by the pinned `random_state` in `anomaly_train`.

## Out of scope for this gate

- No live/streamed data (committed fixture only).
- No drift/retraining evaluation.
- Does not assert anything about downstream triage/response (covered by existing gates + the e2e test).
