# Contract: Anomaly Model + Settings + Emission

**Owner**: `017-ml-anomaly-detector`. **Consumers**: the existing ingestion path (`#4`), unchanged.

## 1. `AnomalyModel` Protocol (`backend/domain/anomaly.py`)

Pure Protocol (mirrors `CorpusRetriever` in `domain/corpus.py`); implemented by infra, faked in tests.

```python
class AnomalyModel(Protocol):
    feature_spec: list[str]                      # ordered feature names
    def score(self, vectors: list[FeatureVector]) -> list[float]: ...  # [0,1], higher = more anomalous
```

- The runner is built with **closure-factory DI** (`make_anomaly_runner(*, settings, session_factory,
  queue, cache, redactor, model)`), so the concrete model is injected — exactly the seam #14's
  `make_detector_runner` uses for its collaborators.

## 2. Saved artifact (`backend/data/anomaly/model.joblib`)

- Produced **offline** by `python -m backend.anomaly_train` (FR-001), pinned `random_state` (reproducible).
- Embeds: fitted `IsolationForest`, ordered `feature_spec`, score-normalization params (R3/R9).
- **Committed** to the repo (small). The full CERT dataset is **not** committed.
- `SklearnAnomalyModel(model_path)` loads it and **fails closed** if missing/unloadable — the runner emits
  no alerts and surfaces a clear error (FR-012), never fires on un-scored input.

## 3. `AnomalySettings` (new section, `extra="forbid"`)

Added to `backend/infra/config.py`, registered as `anomaly` on the `Settings` aggregate.

| Field | Type / default | Purpose |
|---|---|---|
| `enabled` | `bool = True` | gate the one-shot runner |
| `model_path` | `str = "backend/data/anomaly/model.joblib"` | saved artifact source |
| `replay_path` | `str \| None = None` | replayed SIEM log source (JSON/JSONL list) |
| `window` | `str = "1d"` (or `window_seconds: int`) | per-entity aggregation window (research R2) |
| `fire_threshold` | `float = 0.60` (`0..1`) | a window fires iff `score >= fire_threshold` (FR-004/5) |
| `band_medium` / `band_high` / `band_critical` | `float = 0.60 / 0.75 / 0.90` | score→severity bands (FR-004a) |
| `max_events` | `int = 100_000` (`gt=0`) | safety cap on a replay run |
| `source_tag` | `str = "anomaly-detector"` | value passed to `intake.accept(source=...)` (FR-006) |

## 4. Emission contract (the load-bearing "zero downstream change" rule)

- The detector emits **only** through `services/intake.accept(*, alert: WazuhAlert, source: str, ...)`.
- **`intake.accept` is reused unchanged** — its `source: str = "wazuh"` parameter already exists (added by
  #14). #17 passes `source=settings.anomaly.source_tag` (`"anomaly-detector"`) ⇒ `Incident.source ==
  "anomaly-detector"` (FR-006), distinguishable from `"wazuh"` and `"detector"`.
- Emitted alerts are **redacted** by the existing SNAPSHOT boundary inside `accept()` (Constitution III).
- **Replay-safety / idempotency** is the existing dedup fingerprint — the detector adds no second dedup
  authority (FR-013).
- The detector creates `Incident(received)` rows only; it performs **no** status transitions and adds **no
  new FSM edge** (supervisor stays single writer).

## 5. What this contract does NOT touch

- No change to `WazuhAlert`/`NormalizedEvent`/`Incident` schemas (no migration).
- **No change to `intake.accept`** (the `source` seam already exists — unlike #14).
- No new router/endpoint (in-process emission).
- No change to the supervisor, agent stages, or any existing eval gate (FR-014).
