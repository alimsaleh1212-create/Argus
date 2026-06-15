# Phase 0 Research — Consolidated Evaluation Harness (#13)

The spec deferred a set of **config-level values and design choices** to planning. Each is resolved
below in Decision / Rationale / Alternatives form. Nothing here re-opens a spec clarification; these are
the "fix during `/speckit-plan`" items the spec named (catastrophic floor, fixture set size, CI budget,
MinIO key scheme, judge model, nightly cadence/notification).

---

## R1 — Threshold source of truth & gate registry

- **Decision**: `config/eval_thresholds.yaml` stays the single source of truth. The harness parses it
  into `GateSpec[]` (`backend/eval/thresholds.py`) and runs a **registry** keyed by gate name. Every
  declared gate MUST have a registered runner (else **orphan → hard error**); every registered runner
  MUST have a declared gate (else **stale → hard error**). The existing tests in `tests/eval/` are
  refactored to import the same scoring helpers and read their thresholds from the parsed yaml rather
  than from local constants (e.g. triage's `MIN_MACRO_F1`).
- **Rationale**: satisfies FR-002 (no orphan/un-thresholded gate) and FR-011 (no hardcoded divergence)
  with one scoring implementation shared by harness + pytest.
- **Alternatives**: harness shells out to `pytest -k <gate>` and parses pass/fail — rejected: loses the
  numeric score the report needs and couples the harness to pytest output. Duplicate thresholds in code
  and yaml — rejected: the exact drift FR-011 forbids.

## R2 — Rationale judge: rubric, scale, and agreement metric

- **Decision**: A **structured-output** judge (pinned to the cloud primary, Gemini) scores each agent
  rationale on a small ordinal scale — **`grounded` / `partially_grounded` / `ungrounded`** — plus a
  boolean "cites only supplied evidence" check. The judge is first run over the hand-labeled reference
  set; **judge↔human agreement = exact-match rate** on the ordinal label (Cohen's κ reported as a
  secondary figure but not gated, given small N). Per-stage mean score + agreement are written to the
  report.
- **Rationale**: a 3-point ordinal is robust on tiny samples where a 1–5 scale is noise; exact-match
  agreement is interpretable and hand-rollable (no `scikit-learn`). Pinning the judge to one strong model
  (R6) keeps "is the rationale good" separate from "is the judge weaker on this provider."
- **Alternatives**: 1–5 Likert (over-precise for N≈5/stage); free-text judge (not machine-scorable);
  κ as the gate (unstable at small N). All rejected for this milestone; revisitable when the labeled set grows.

## R3 — Catastrophic floor (the only blocking part of the reported-only gate)

- **Decision**: The rationale gate is reported-only **except** a catastrophic floor that blocks CI:
  `mean_grounded_rate < 0.50` on **any** stage **OR** `judge_human_agreement < 0.50`. Committed in the
  yaml `rationale` block as `catastrophic_floor: {min_grounded_rate: 0.5, min_judge_agreement: 0.5}`.
- **Rationale**: catches "the judge is untrustworthy" or "rationales are mostly ungrounded" — true
  regressions — while letting ordinary probabilistic wobble pass (FR-004). 0.50 = "worse than a coin
  flip," an unambiguous floor, not a quality bar.
- **Alternatives**: no floor (a silently broken judge never trips) — rejected; a high blocking threshold
  (re-introduces the flaky merge gate the clarification rejected) — rejected.

## R4 — Hand-labeled rationale fixture set size

- **Decision**: **5 reference rationales per stage** (triage/enrichment/response = 15 total), each
  `{incident_context, rationale_text, human_label, cites_supplied_evidence}`, under
  `tests/fixtures/rationale/{stage}.json`. Reuse existing incident/triage fixtures for context where
  possible; hand-label the rationale field.
- **Rationale**: matches the brief's "hand-label a few"; enough to compute a stable exact-match agreement
  while staying inside the ≤400-line M3 PR and the memory budget.
- **Alternatives**: 1–2 per stage (agreement statistically meaningless); 20+ (labeling cost, PR bloat) —
  both rejected for v1; the set is additive later.

## R5 — Per-PR provider mechanics (Ollama) & CI time budget

- **Decision**: The per-PR `eval` job runs **(a)** all deterministic/provider-independent gates
  (supervisor_routing, retrieval, temporal_memory, redaction) and **(b)** the LLM gates
  (triage, llm_provider) against **Ollama only**, using the existing compose `ollama` service with a
  **small pinned tag pre-pulled and layer-cached** in the workflow. Target budget: the `eval` job adds
  **≤ ~6 min** to per-PR CI. The rationale judge does **not** run per-PR (freeze/nightly only).
- **Rationale**: honors the clarification (per-PR single provider = local Ollama, no cloud key,
  fork-safe). Pre-pull + cache keeps Ollama startup off the critical path. Excluding the judge per-PR
  keeps the blocking path deterministic-plus-one-cheap-provider.
- **Alternatives**: per-PR mocked LLM (rejected by the spec clarification — wanted real single-provider
  signal); per-PR Gemini (needs a live key on every PR incl. forks; cost) — rejected. *Fallback noted:*
  if Ollama-in-CI proves too slow/flaky to gate on, downgrade the per-PR Ollama gates to **non-blocking
  (reported)** while keeping the deterministic set blocking — a tuning knob, recorded if exercised.

## R6 — Rationale judge provider & both-providers matrix execution

- **Decision**: Judge is **pinned to Gemini** and runs only at freeze/nightly. The both-providers matrix
  applies to the **rationale producers** (triage/enrichment/response run on Gemini and on Ollama;
  the pinned judge scores both sets) and to the **triage/llm_provider** gates (each evaluated per
  provider). A **required** LLM gate failing on **either** provider → freeze "not certifiable" (FR-007).
- **Rationale**: matches the clarification (pinned judge, both producers). The matrix is a config list
  (`providers_freeze: [gemini, ollama]`), so a third provider later extends it without code change.
- **Alternatives**: judge on both providers (confounds judge quality with rationale quality, ~2× cost);
  self-judge (self-grading bias) — both rejected per the clarification.

## R7 — Report shape, MinIO key scheme & retention

- **Decision**: One `eval_report.json` per run (schema in [contracts/eval-report.schema.json](contracts/eval-report.schema.json)).
  Persisted to the reserved **`eval-reports`** bucket via `infra/blob.py` `BlobClient.put_object`,
  keyed **`reports/{commit_sha}/{run_id}.json`**; freeze runs additionally copy to
  **`freezes/{git_tag}/eval_report.json`**. **History is retained** (unique keys, never overwritten) per
  FR-009/SC-004; a small `reports/index.jsonl` append captures run metadata for trend queries. Upload
  failure → run marked `incomplete` and the job fails (the artifact *is* the freeze deliverable).
- **Rationale**: the bucket already exists in `MinioSettings.buckets`; commit/run keying gives the
  "getting better over time" trend and audit the clarification chose; the freeze copy gives a stable
  tag-addressable artifact for the submission.
- **Alternatives**: latest-only overwrite (no trend/audit — rejected by clarification); a DB table
  (premature; MinIO is the brief's named home for eval reports).

## R8 — Nightly/freeze trigger & failure notification

- **Decision**: `.github/workflows/eval-freeze.yml` triggers on **`schedule`** (nightly cron),
  **`workflow_dispatch`** (manual freeze), and **`push: tags: ['v*']`** (the freeze tag). It stands up
  the compose stack (MinIO + Ollama) and `GEMINI_API_KEY` from repo secrets, runs
  `python -m backend.eval --freeze --providers gemini,ollama --upload`, and on failure relies on
  **GitHub's native failed-scheduled-workflow notification** (no new alerting infra in v1).
- **Rationale**: smallest correct mechanism; reuses the turnkey compose bring-up; nightly catches
  provider drift between freezes; tag-push makes the freeze report a release artifact.
- **Alternatives**: a bespoke notifier/Slack webhook (new infra, out of v1 scope); freeze-only (no
  nightly drift detection — weaker signal). Both rejected.

## R9 — Memory-safety of the eval run

- **Decision**: `scripts/run-evals.sh` mirrors `scripts/run-tests.sh` — heavy gates
  (redaction→spaCy/Presidio, retrieval/temporal→graphiti imports) run **one gate per subprocess** so peak
  memory ≈ one gate. The harness itself supports `--gate <name>` so the script can fan out per gate.
- **Rationale**: the project's documented OOM cause (spaCy + graphiti never freed within a process)
  applies identically to eval; the established batched-subprocess pattern is the proven fix (FR-013).
- **Alternatives**: one in-process run (OOMs on constrained machines — the exact failure run-tests.sh
  was built to avoid). Rejected.

## R10 — `pyyaml` as a direct dependency

- **Decision**: Add `pyyaml>=6` to `pyproject.toml` `dependencies` (currently only a transitive lock
  entry). Pin via `uv.lock`.
- **Rationale**: the harness imports `yaml` directly to read thresholds; Constitution VII requires pinned,
  declared deps — relying on a transitive provider is fragile.
- **Alternatives**: hand-parse the yaml (reinvents a parser); `tomllib` + convert thresholds to TOML
  (needless churn of a committed file other components extend). Rejected.

---

## Resolved unknowns

All Technical-Context items are concrete; **no `NEEDS CLARIFICATION` remain**. The single tracked
exception (Constitution III red-team deferral, VD1) is recorded in `plan.md` Complexity Tracking and
depends on a separate `/speckit-constitution` amendment before the freeze tag.
