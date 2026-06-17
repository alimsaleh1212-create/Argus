# Phase 1 Data Model: ML Anomaly Detection Layer (#17)

Pure **Pydantic v2** types in `backend/domain/anomaly.py` (`extra="forbid"`, `frozen=True` where natural),
**no outward imports** except `Severity` from `domain/incident.py` (domain→domain allowed under the
isolation contract) and `typing.Protocol`. **No persistence model — no migration.** The detector reuses
the existing `Incident`/`WazuhAlert` schema for emission, exactly like #14.

---

## EntityActivityWindow (the scoring unit)

A single entity's (user's) replayed log activity aggregated over a configured window. Built from raw log
records; it is the **window**, not the event, that is scored (Q1 / research R2).

| Field | Type | Notes |
|---|---|---|
| `entity_id` | `str` | the user/host the window belongs to (e.g. CERT `user` id) |
| `window_start` | `datetime` | inclusive window start (drives reproducible binning — no wall-clock) |
| `window_end` | `datetime` | exclusive window end (`window_start + AnomalySettings.window`) |
| `features` | `dict[str, float]` | named behavioral features (after-hours logons, distinct PCs, removable copies, external email, flagged http, …) |
| `raw_event_count` | `int` | number of source records aggregated (provenance / evidence) |

- Validation: `window_end > window_start`; `features` keys must match the artifact's feature spec at score
  time (missing → 0.0, extra → dropped, both logged) so train/serve featurization can never silently skew.

## FeatureVector

The ordered, model-ready numeric vector derived from a window's `features`.

| Field | Type | Notes |
|---|---|---|
| `entity_id` | `str` | carried through for attribution |
| `values` | `list[float]` | ordered per the artifact's feature spec (order is load-bearing) |

- `services/anomaly.featurize(window, feature_spec) -> FeatureVector` produces this. The same function is
  used at train time and replay time (structural zero train/serve skew, R2).

## AnomalyModel (Protocol — pure)

Defined in `domain/anomaly.py`; implemented by `infra/anomaly_model.SklearnAnomalyModel` and faked in
tests. Mirrors the `CorpusRetriever` Protocol pattern (`domain/corpus.py`).

```python
class AnomalyModel(Protocol):
    feature_spec: list[str]                      # ordered feature names the model expects
    def score(self, vectors: list[FeatureVector]) -> list[float]: ...
        # returns a [0,1] anomaly score per vector (higher = more anomalous), R3
```

- The concrete `SklearnAnomalyModel` loads `model.joblib` (Isolation Forest + feature spec + normalization
  params), applies `score_samples`, negates + min-max-normalizes using the **saved** params (R3), and
  returns `[0,1]` scores.

## ScoreBands (config — severity mapping)

Config-backed score→severity mapping (Q2 / research R5), part of `AnomalySettings`.

| Field | Type / default | Notes |
|---|---|---|
| `fire_threshold` | `float = 0.60` (`0<=x<=1`) | a window fires iff `score >= fire_threshold` (FR-004/FR-005) |
| `band_medium` | `float = 0.60` | `score >= band_medium → medium` |
| `band_high` | `float = 0.75` | `score >= band_high → high` |
| `band_critical` | `float = 0.90` | `score >= band_critical → critical` |

- `services/anomaly.score_to_severity(score, bands) -> Severity` applies the bands (descending);
  below `fire_threshold` no alert is emitted. Reuses `Severity` from `domain/incident.py`.

## AnomalyFinding

The pure output of the scoring pass — one per window over `fire_threshold`, before mapping to the
ingestion contract.

| Field | Type | Notes |
|---|---|---|
| `entity_id` | `str` | affected user/host |
| `score` | `float` | `[0,1]` anomaly score |
| `severity` | `Severity` | from `score_to_severity` (R5) |
| `window` | `EntityActivityWindow` | the originating window (evidence: features + time range) |
| `top_features` | `list[str]` | the highest-contributing feature names (evidence; FR-003/FR-015) |

## Mapping: AnomalyFinding → WazuhAlert (emission contract)

`services/anomaly.finding_to_wazuh_alert(finding) -> WazuhAlert` builds the **existing** ingestion type
(`domain/incident.py`), mirroring #14's `fired_alert_to_wazuh_alert`:

- `WazuhRule(id="anomaly-ueba", level=<severity→level via #14's _SEVERITY_TO_LEVEL>,
  description="Behavioral anomaly: <entity> deviates from baseline (score=<s>)", groups=["ueba","anomaly"])`
- `WazuhAlert.data` = the window's `features` + `entity_id` + `score` + `top_features` (the evidence the
  triage agent reasons over; it is an **anomaly score + contributing features**, not a rule identity —
  FR-015)
- `WazuhAlert.full_log` = a compact deterministic summary (`anomaly: entity=… score=… window=…`)
- `WazuhAlert.agent` = `WazuhAgent(name=entity_id)` (best-effort attribution)

Emission then calls `intake.accept(..., alert=<WazuhAlert>, source="anomaly-detector")` (R7).

## Saved artifact (`backend/data/anomaly/model.joblib`)

Not a domain type — the persisted training output (research R9). Contains: the fitted `IsolationForest`,
the ordered `feature_spec`, and the score normalization params (min/max of negated `score_samples`). Loaded
by `SklearnAnomalyModel`. Committed (small); regenerable offline via `anomaly_train` with a pinned seed.

## State / lifecycle

- The detector is **stateless across runs** (one-shot). Windows are built within a single replay pass,
  binned by `event_time` (no wall-clock) for reproducibility.
- It creates **new** `Incident` rows (status `received`) exactly as any alert source does; it has **no**
  authority over incident state transitions (supervisor remains single writer; **no new FSM edge**).
- **Idempotency / replay-safety** is delegated to the existing dedup fingerprint in `intake.accept()`
  (FR-013) — re-running the same replay set creates no duplicates.
