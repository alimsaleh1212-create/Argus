# Quickstart: ML Anomaly Detection Layer (#17)

Backend-only, **no migration**. A second decoupled detection source (UEBA-style) that fires alerts into
the existing ingestion path; everything downstream is unchanged. Real ML, mock environment.

## Train the model (offline, one-time — not on the request path)

```bash
# Requires the CERT Insider Threat dataset (r6.2) locally; NOT committed to the repo.
# Uses the dev/training dependency group (pandas).
uv run python -m backend.anomaly_train \
  --cert-dir /data/cert-r6.2 \
  --out backend/data/anomaly/model.joblib \
  --seed 42
# Deterministic given --seed: regenerates the identical committed artifact.
```

The artifact (`model.joblib`) embeds the fitted Isolation Forest, the feature spec, and score
normalization params. It is **committed** so CI/eval/demo never retrain (R9).

## Run the anomaly detector over replayed logs

```bash
# Stack up (Postgres + Redis + API/worker) as usual
docker compose up -d

# One-shot inference run: loads the saved model, replays SIEM logs, scores per-entity windows,
# fires alerts for windows over the threshold into ingestion.
uv run python -m backend.anomaly_detector \
  --model backend/data/anomaly/model.joblib \
  --replay tests/fixtures/anomaly/replay/scenarios.jsonl
# (paths default to AnomalySettings.model_path / replay_path if omitted)
```

Each window over `fire_threshold` becomes an `Incident(source="anomaly-detector")` that flows through
triage → enrichment → response exactly like a Wazuh- or rule-detector-sourced alert. Re-running the same
replay set creates **no** duplicates (existing dedup fingerprint). If the artifact is missing/unloadable,
the run **fails closed** (no alerts, clear error — FR-012).

## Verify

```bash
# Anomaly-sourced incidents exist and ran the pipeline
#   (look for source="anomaly-detector" in the incident queue / dashboard)

# Normal-behavior windows produced no incident (low false-positive rate)

# Both detectors coexist: run #14 then #17 over the same source; each incident is attributable
#   to source="detector" vs source="anomaly-detector" (layering, not replacement)
```

## Run the anomaly-detection eval gate

```bash
# Deterministic, provider-independent — precision/recall + FP-rate on the labeled fixture,
# scored against the committed artifact (no retraining).
uv run python -m backend.eval --gate anomaly_detection
```

Passes iff `precision >= precision_min`, `recall >= recall_min`, and `false_positive_rate <=
max_false_positive_rate` (see `config/eval_thresholds.yaml::gates.anomaly_detection`).

## Tests (memory-safe runner — never one big pytest)

```bash
make test-unit          # featurize, score→severity bands, finding→WazuhAlert mapping, fail-closed (FakeAnomalyModel)
make test-integration   # anomaly_detector -> intake.accept -> incident persisted/enqueued; source tag; dedup
make test-e2e           # replayed anomalous window detected -> full pipeline terminal
```

Unit/integration tiers inject a **`FakeAnomalyModel`** — they do not load `scikit-learn` or the real
artifact. Only the `anomaly_detection` eval gate loads the real model.

## Tune the operating point (config-only)

Adjust `AnomalySettings.fire_threshold` and the `band_*` breakpoints (env or settings) to shift the
precision/recall point and severity mapping — no code change (SC-006).

## Honesty note

The model is **trained offline on a public dataset (CERT r6.2)** and inference runs over **replayed logs**,
not a live Wazuh stream. It raises recall on novel **behavior** (compromised credentials, lateral
movement, insider exfil), **not** literal zero-day exploits. **No real-time / production-efficacy claim is
made.** It **complements**, and does not replace, the deterministic rule detector (#14).
