# ML Anomaly Detector — Mechanism Clarification (#17)

Answers to common questions about the trained model in `017-ml-anomaly-detector`. For the *why this
file layout* rationale (no standalone `ml/` dir, sklearn confined to `infra/`), see
[siem-ml-detector.md §10](siem-ml-detector.md#10-code-structure--backend-across-layers-not-a-standalone-ml-dir).
For copy-pasteable run commands, see [`specs/017-ml-anomaly-detector/quickstart.md`](../specs/017-ml-anomaly-detector/quickstart.md).

## 0. File map — where everything lives

The code is deliberately spread across `backend`'s existing layers (not grouped into one folder) so it
needs no new import-linter contract. Quick lookup:

| What you're looking for | File | Layer |
|---|---|---|
| Pure types + `AnomalyModel` Protocol (`EntityActivityWindow`, `FeatureVector`, `AnomalyFinding`, `ScoreBands`) | [`backend/domain/anomaly.py`](../backend/domain/anomaly.py) | `domain` (pure, no I/O) |
| Window building, featurization, score→severity, `AnomalyFinding`→`WazuhAlert` mapping | [`backend/services/anomaly.py`](../backend/services/anomaly.py) | `services` (pure, no model object) |
| sklearn wrapper — loads the artifact, runs `score_samples`, normalizes to `[0,1]` | [`backend/infra/anomaly_model.py`](../backend/infra/anomaly_model.py) | `infra` (owns the sklearn/joblib import — the *only* legal importer per the import-linter contract) |
| Offline trainer CLI — `python -m backend.anomaly_train` | [`backend/anomaly_train.py`](../backend/anomaly_train.py) | top-level one-shot entrypoint (sibling of `backend/detector.py`) |
| Replay runner CLI — `python -m backend.anomaly_detector` | [`backend/anomaly_detector.py`](../backend/anomaly_detector.py) | top-level one-shot entrypoint, `make_anomaly_runner` closure-factory DI |
| Committed trained artifact (model + feature_spec + normalization params) | `backend/data/anomaly/model.joblib` | data (next to `backend/data/detector/rules.yaml`) |
| Settings (`enabled`, `model_path`, `replay_path`, `window`, `fire_threshold`, bands, `max_events`) | `AnomalySettings` in [`backend/infra/config.py`](../backend/infra/config.py) | `infra` config |
| Eval gate (precision/recall/FP-rate, registered in `GATE_REGISTRY`) | [`backend/eval/gates/anomaly_detection.py`](../backend/eval/gates/anomaly_detection.py) | `eval/gates` (next to `gates/detection.py`) |
| Labeled fixture feeding unit/integration/e2e + the eval gate | `tests/fixtures/anomaly/replay/scenarios.jsonl` | test fixtures (one shared set, not forked per tier) |
| Deterministic test double (no sklearn load) | `FakeAnomalyModel` in `tests/helpers/anomaly.py` / `tests/conftest.py` | test helper |

**No router / no live endpoint** — this is a one-shot replay command, not a service. It emits findings
**in-process** through the existing `services/intake.accept(source="anomaly-detector")` seam into the
same `/ingest` pipeline #14 uses; there's nothing to call over HTTP.

## 1. Original dataset — where is it?

**Not committed to the repo** — by design (per `DECISIONS.md`/CLAUDE.md, the full CERT dataset is
explicitly excluded). The trainer at [`backend/anomaly_train.py`](../backend/anomaly_train.py) expects
a local copy of the **CERT Insider Threat Dataset r6.2** (a public CMU/SEI UEBA research dataset —
`logon.csv`, `device.csv`, `file.csv`, `email.csv`, `http.csv`), supplied via `--cert-dir`:

```
python -m backend.anomaly_train --cert-dir <path-to-cert-r6.2> --out backend/data/anomaly/model.joblib --seed 42
```

What **is** committed is the trained artifact itself: `backend/data/anomaly/model.joblib` (~1.2MB) —
the model works out of the box without re-running training. The held-out labeled eval set is a small
synthetic stand-in fixture at `tests/fixtures/anomaly/replay/scenarios.jsonl` (hand-labeled
`malicious`/`normal` windows), not real CERT data.

## 2. Target / classes to predict

This is **unsupervised anomaly detection**, not classification — there is no labeled target column at
training time. `IsolationForest` fits on per-entity-per-window behavioral feature vectors and produces a
continuous **anomaly score in `[0,1]`** (higher = more anomalous). The score is thresholded into:

- **No alert** (score < `fire_threshold`, default `0.60`)
- **LOW** / **MEDIUM** / **HIGH** / **CRITICAL** severity bands (config-backed cutoffs at
  `0.60`/`0.75`/`0.90` — see `domain/anomaly.py::ScoreBands`)

The only place `malicious`/`normal` labels exist is the **eval fixture**, used solely to score
precision/recall after the fact — they are never fed into the model as a supervised target.

## 3. Features (the "X")

10 per-entity, per-window (default `1d`) behavioral counts, built in
`services/anomaly.py::_aggregate_features` / `_CANONICAL_FEATURE_NAMES`:

```
logon_count, device_count, file_count, email_count, http_count,
distinct_pc, after_hours_count, removable_copy_count,
external_email_count, flagged_http_count
```

The same `featurize()` function is used at train time and inference time — this guarantees zero
train/serve skew.

## 4. Metrics

From the `anomaly_detection` eval gate (`config/eval_thresholds.yaml` +
`backend/eval/gates/anomaly_detection.py`):

- **Precision** ≥ 0.80
- **Recall** ≥ 0.80
- **False-positive rate** ≤ 0.10

Computed via TP/FP/FN/TN over the labeled fixture windows at the configured `fire_threshold`, scored
deterministically against the **committed** artifact (no training randomness at gate-check time). Run:

```
uv run python -m backend.eval --gate anomaly_detection
```

## 5. Model mechanism

**Isolation Forest** (`sklearn.ensemble.IsolationForest`, 100 trees, `contamination=0.05`, fixed
`random_state` seed for reproducibility — `backend/anomaly_train.py::train_anomaly_model`).

Mechanism: it builds random binary trees that recursively partition the feature space; anomalies, being
"few and different," get isolated in fewer splits (shorter average path length) than normal points.
`score_samples()` gives higher values to normal points, so the code **negates** it, then
**min-max normalizes** using `score_min`/`score_max` computed once on the training set and saved in the
artifact — so inference-time scaling exactly reproduces training-time scaling
(`backend/infra/anomaly_model.py::SklearnAnomalyModel.score`). The artifact (`model.joblib`) bundles the
fitted model + `feature_spec` + those normalization params together; loading is fail-closed
(missing/corrupt artifact → no alerts emitted, never a crash).
