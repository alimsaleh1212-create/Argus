# Quickstart: Deterministic Rule/Threshold Detector (#14)

Backend-only, no migration. The detector is a one-shot command that fires alerts into the existing
ingestion path; everything downstream is unchanged.

## Run the detector over a replay set

```bash
# Stack up (Postgres + Redis + API/worker) as usual
docker compose up -d

# One-shot detection run: reads rules + replay events, fires matched alerts into ingestion
uv run python -m backend.detector \
  --rules backend/data/detector/rules.yaml \
  --replay tests/fixtures/detector/replay/scenarios.json
# (paths default to DetectorSettings.rules_path / replay_path if omitted)
```

Each matched event becomes an `Incident(source="detector")` that flows through
triage → enrichment → response exactly like a Wazuh-sourced alert. Re-running the same replay set
creates **no** duplicates (existing dedup fingerprint).

## Verify

```bash
# Detector-sourced incidents exist and ran the pipeline
#   (look for source="detector" in the incident queue / dashboard)

# Benign events produced no incident (suppression / no over-firing)
```

## Run the detection eval gate

```bash
# Deterministic, provider-independent — precision/recall on the labeled replay set
uv run python -m backend.eval --gate detection
```

Passes iff `precision >= precision_min` and `recall >= recall_min` (see
`config/eval_thresholds.yaml::gates.detection`).

## Tests (memory-safe runner — never one big pytest)

```bash
make test-unit          # rule match/threshold logic, mapping, malformed-skip
make test-integration   # detector -> intake.accept -> incident persisted/enqueued; source tag; dedup
make test-e2e           # replayed event detected -> full pipeline terminal
```

## Add a new rule (config-only — SC-005)

Edit `backend/data/detector/rules.yaml`, add a `match` or `threshold` rule (see
`contracts/detector-rules-contract.md`), re-run the detector. No code change.

## Honesty note

The detector runs over **replayed** events (no live Wazuh, no network capture). The pipeline behaves
identically to live ingestion. No real-time/production-efficacy claim is made.
