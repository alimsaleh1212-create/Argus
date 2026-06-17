# Phase 0 Research: ML Anomaly Detection Layer (#17)

All decisions below resolve the spec's plan-deferred items (model library, CERT version, window length,
threshold/band values, artifact storage, runtime/packaging) and confirm the four clarification answers
already recorded in `spec.md` (§Clarifications). No `NEEDS CLARIFICATION` remain.

The guiding principle: **mirror the deterministic detector (#14) as closely as possible** — one-shot
runner + pure core + closure-factory DI + emission through the existing `intake.accept(source=...)` seam
— so #17 is a *second decoupled source* with the same zero-downstream-change footprint, differing only
in that "does this fire?" is answered by a saved ML model instead of a YAML rule set.

---

## R1 — Component shape: one-shot runner + pure core + injected model (mirrors #14)

**Decision.** Two one-shot commands plus a pure service core and an infra model wrapper:

- `python -m backend.anomaly_train` — **offline** training entrypoint (build-time, never on the request
  path; FR-001). Reads the CERT dataset, builds per-entity-window features, fits an Isolation Forest with
  a pinned seed, saves the artifact.
- `python -m backend.anomaly_detector` — the **replay/inference** runner (mirror of `backend/detector.py`):
  closure-factory `make_anomaly_runner(...)` injects the model + intake collaborators, loads the saved
  artifact + replayed logs, builds entity-windows, scores them, and emits an alert for each window over
  the fire threshold via `intake.accept(source="anomaly-detector")`.
- `backend/services/anomaly.py` — the **pure** core (mirror of `services/detector.py`): `build_windows`,
  `featurize`, `score_to_severity`, `finding_to_wazuh_alert`, `load_replay_events`. No I/O, no model
  object — fully unit-testable.
- `backend/infra/anomaly_model.py` — `SklearnAnomalyModel` implementing the `AnomalyModel` Protocol
  (defined pure in `domain/anomaly.py`); owns the `scikit-learn` + `joblib` imports (infra owns external
  SDKs, like `infra/llm_drivers.py` / `infra/intel.py`).

**Rationale.** Keeps everything inside the existing layers (`domain` isolated, `services` pure, `infra`
owns external libs, top-level one-shot commands like `backend/detector.py` / `backend/worker.py`) — **no
new top-level package, no new import-linter contract**. The `AnomalyModel` Protocol makes the model an
injected dependency, so unit/integration tests use a deterministic `FakeAnomalyModel` and never load
`scikit-learn` or the real artifact (also sidesteps the spaCy+graphiti OOM constraint — tests stay light).

**Alternatives considered.** A new `backend/ml/` top-level package — rejected: forces a new import-linter
contract for zero benefit; the pure transforms belong in `services`, the model wrapper in `infra`.

---

## R2 — Detection granularity: per-entity time window (confirms Q1)

**Decision.** The scoring unit is a **per-entity (user) activity window**. Raw CERT log records for a user
over a configured window (default **one day per user**, `AnomalySettings.window`) are aggregated into a
fixed behavioral feature vector, and the **window** is scored — individual log events are never scored in
isolation.

**Rationale.** This is the UEBA standard and the only granularity at which CERT's insider-threat scenario
labels are meaningful (a single logon is not anomalous; the day's *pattern* is). Per-user-day also yields
a hand-labelable held-out fixture and a clean precision/recall definition (one window = one classification).

**Feature set (per user-day, illustrative — finalized in `backend/anomaly_train.py`).** After-hours logon
count, distinct PCs accessed, removable-device connects, file copies to removable media, emails to
external domains, attachment count/size, http requests to flagged categories (job-search / cloud-storage /
leak sites), off-hours activity ratio. ~10–20 numeric features, standardized. Identical featurization code
path (`services/anomaly.featurize`) is used at train time and replay time so train/serve skew is structural
zero.

---

## R3 — Model: Isolation Forest (sklearn), saved with joblib

**Decision.** **Isolation Forest** (`sklearn.ensemble.IsolationForest`), trained offline with
`random_state` pinned, persisted with `joblib` to a small artifact. A compact autoencoder is **not** used.

**Rationale.** Isolation Forest is lightweight, CPU-only, GPU-free, trains in seconds–minutes on CERT
user-day features, has a tiny artifact (KBs–low MBs), is deterministic given a pinned seed, and needs no
deep-learning runtime. It is the canonical choice for CERT insider-threat anomaly demos. An autoencoder
would pull in `torch` (heavy image, GPU temptation) for no recall benefit at this scale.

**Anomaly score.** `IsolationForest.score_samples` returns higher = more normal; we use the **negated,
min-max-normalized** value as a `[0,1]` anomaly score (higher = more anomalous) so the fire threshold and
severity bands are intuitive and config-backed. Normalization parameters are fit at train time and saved
**inside the artifact** so replay scoring reproduces training-time scaling exactly.

**Alternatives considered.** Autoencoder (torch) — rejected (weight/GPU). One-Class SVM — rejected (scales
poorly, less interpretable score). DBSCAN — rejected (clustering, not a per-window score; the published
Wazuh hybrid pairs it with RF for a different pipeline shape).

---

## R4 — Dataset: CERT Insider Threat r6.2 (confirms Q4)

**Decision.** Train and evaluate on the **CERT Insider Threat dataset, release r6.2** (scenario-labeled
user-activity logs: logon, device, file, email, http; entities = users). The **full dataset is offline
only and is NOT committed** to the repo (it is ~GBs). What **is** committed:

- the trained model artifact (`backend/data/anomaly/model.joblib`, small),
- a small **held-out labeled replay fixture** (`tests/fixtures/anomaly/replay/scenarios.jsonl`) — a
  downsampled set of normal + scenario-malicious user-windows for the eval gate, e2e, and demo.

**Rationale.** Cleanest labels, narrative-friendly (the "compromised credential / insider exfil" story),
trains in minutes with Isolation Forest. Keeping the heavy dataset out of git while committing the small
artifact + fixture keeps `docker compose up` from a fresh clone turnkey and the eval reproducible without
the dataset (see R9). r6.2 is the commonly used release with the richest scenario coverage.

**Alternatives considered.** LANL auth/DNS/process — richer lateral-movement story but ~1.6B events,
extreme class imbalance (~0.00007% malicious), heavier feature engineering; documented as the fallback.
Network-flow sets (UNSW-NB15, CIC-IDS2017) — rejected: packets, not logs (wrong framing).

---

## R5 — Severity: config-backed score→severity bands (confirms Q2)

**Decision.** The emitted alert's severity is derived **deterministically** from the `[0,1]` anomaly score
via **config-backed bands** in `AnomalySettings` (e.g. `>=0.90 → critical`, `>=0.75 → high`,
`>=0.60 → medium`, else `low`). A separate **`fire_threshold`** (default `0.60`, the lowest band's lower
bound) gates whether a window fires at all (FR-004/FR-005). The chosen `Severity` maps to a Wazuh `level`
using the **same midpoint approach #14 uses** (`_SEVERITY_TO_LEVEL`) so `intake.accept()` re-derives the
identical `Severity` downstream.

**Rationale.** Deterministic, tunable without code (Constitution VII), preserves the score's signal for
supervisor fast-path routing, and reuses #14's proven severity→level inverse so there is genuinely zero
downstream change. Threshold and bands are the config-backed values the roadmap said to fix at plan time.

---

## R6 — Eval gate: `anomaly_detection`, blocking/required, provider-independent (confirms Q3)

**Decision.** A new **deterministic, provider-independent** `anomaly_detection` gate in
`backend/eval/gates/anomaly_detection.py`, **`required: true` (blocking)**. It loads the **committed
artifact** via the real `SklearnAnomalyModel`, scores the **committed held-out labeled fixture**, and
computes **precision, recall, and false-positive rate**. Declared in `config/eval_thresholds.yaml` **and**
registered in `GATE_REGISTRY` **and** imported in `backend/eval/__main__.py` in the same change (orphan/stale
mismatch is a hard error, exit 2, per #13). Seed thresholds: `precision_min: 0.80`, `recall_min: 0.80`,
`max_false_positive_rate: 0.10` (a target informed by the published ~0.97/<0.1 hybrids — *a target, not a
commitment*; tighten once the fixture is finalized).

**Rationale.** Directly measures SC-002/SC-003. Blocking is justified because the gate scores the *saved
artifact* deterministically (R9) — same artifact + same fixture → same score, no runtime variance — so
there is no flakiness argument to soften it (matches #14's `detection` gate posture and Constitution II).
Provider-independent because the detector runs no LLM.

**Note.** This gate is the **one place** that imports `scikit-learn` in CI's eval job. The unit/integration
tiers use the `FakeAnomalyModel` and do not.

---

## R7 — Emission: reuse `intake.accept(source="anomaly-detector")` — zero new code in the path

**Decision.** The runner emits **in-process** via the existing `services/intake.accept(*, alert, source,
...)`. The `source` parameter **already exists** (added by #14); #17 passes
`source=settings.anomaly.source_tag` (`"anomaly-detector"`), so `Incident.source == "anomaly-detector"`
(FR-006), distinguishable from `"wazuh"` and `"detector"`.

**Rationale.** `accept()` already does redact → dedup → persist → enqueue; reusing it gives anomaly alerts
redaction for free (Constitution III SNAPSHOT boundary) and replay-safety via the existing dedup
fingerprint (FR-013). **Unlike #14, #17 needs no change to `intake` at all** — the seam is already
parameterized. **No migration, no schema change, no new router.**

**Alternatives considered.** HTTP POST to `/ingest/wazuh` — rejected for the same reasons #14 rejected it
(token + running API + round-trip for zero benefit in a replay tool).

---

## R8 — Runtime/packaging: reuse the backend image; add scikit-learn + numpy (resolves the v3a "own image" sketch)

**Decision.** **Reuse the single backend image** (the "one image, many containers" decision, D-platform).
The two anomaly commands run as one-shot containers (same venv as API/worker/migrate/detector). Add
**`scikit-learn>=1.5`** and **`numpy>=1.26`** as **runtime** dependencies (inference needs them);
`joblib` ships with scikit-learn. Add **`pandas>=2.2`** as a **dev/training-only** dependency group
(offline CERT wrangling in `anomaly_train`, never imported on the replay/serve path).

**Rationale.** Isolation Forest is CPU-only and tiny — the v3a "its own runtime/image, like the React SPA"
note was written when #17 was scoped as a *separate project*; brought in-project, it needs no separate
image. Keeping pandas out of the runtime keeps the serving image lean. `import-linter`: `infra` may import
`scikit-learn`/`joblib`; `domain` stays pure (Protocol only); the no-bypass import guard (currently
covering opentelemetry/presidio) is extended to keep `scikit-learn` confined to `infra` + the
`anomaly_train`/eval entrypoints.

---

## R9 — Model-artifact storage & reproducibility

**Decision.** Commit the small trained artifact to **`backend/data/anomaly/model.joblib`** (the artifact
embeds the fitted Isolation Forest, the feature spec/order, and the normalization params). The eval gate,
e2e, and demo all run against this committed artifact. Training reproducibility is via the **pinned
`random_state`** in `anomaly_train` (re-runnable offline against CERT to regenerate the identical artifact).

**Rationale.** Satisfies FR-010/SC-008: CI never retrains — it deterministically *scores* the committed
artifact against the committed fixture, so results are bit-stable and the heavy dataset is not needed in
CI or at demo time. Committing a small (<a few MB) model is the simplest turnkey path; MinIO storage was
considered but adds a seed step for no benefit at this size.

---

## R10 — Constitution IV exception (recorded) + scope decision

**Decision.** Record, **before implementation lands**, (a) a `DECISIONS.md` entry and (b) a constitution
note/amendment acknowledging that #17 is ML at the **detection** layer — an explicit, time-bound exception
to Principle IV and to the Scope-Discipline line that lists "an ML anomaly detector (roadmap v2a/v3)" as
v1-out-of-scope. The exception is bounded: determinism-first is **preserved on the response path**; the
detector is **decoupled** (no second writer, no new FSM edge); it **complements** (does not replace) #14.

**Rationale.** This is the one place #17 differs from #14's clean Constitution-IV alignment. The brief's
2026-06-16 Detection Strategy Update explicitly anticipates and authorizes this; Governance requires it be
recorded as a justified exception rather than silently built. See Constitution Check + Complexity Tracking
in `plan.md`. This is a **precondition task** for M-a.

---

## R11 — Out-of-scope confirmations (unchanged from spec)

- No live SIEM/log feed or real-time scoring (v3c); replayed logs only.
- No concept-drift monitoring or in-production retraining (documented future; the replay demo claims no
  drift handling and no real-time efficacy).
- No feed-to-detector tuning of the ML model (the `016-M2` analog) — future.
- No XDR fusion (#18/v3) — #17 merely *provides* the second source.
- No change to the `Incident` schema, supervisor FSM, agent stages, or existing eval gates (beyond the
  additive new `anomaly_detection` gate). `intake.accept` is reused **unchanged** (its `source` param
  already exists).
