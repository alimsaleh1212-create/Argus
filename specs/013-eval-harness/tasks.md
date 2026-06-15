---

description: "Task list for Consolidated Evaluation Harness & CI Gates (#13)"
---

# Tasks: Consolidated Evaluation Harness & CI Gates

**Input**: Design documents from `specs/013-eval-harness/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md), [data-model.md](data-model.md), [contracts/](contracts/)

**Tests**: REQUIRED (Constitution II — Test-First, Three-Tier, Eval-Gated). Within each phase, write the
test task **before** its implementation task and confirm it FAILS first.

**Constitution**: v2.0.0 — the red-team / injection gate is **deferred to v3b** (VD1). This spec reserves
its seam and adds **no** injection coverage (see T044).

**Milestones (Constitution I — commit at each, ≤ ~400-line PRs)**:
- **M1** = Phase 1 + Phase 2 + US1 + US2 (harness + CI deterministic + local runner)
- **M2** = US3 (both-providers freeze + report → MinIO)
- **M3** = US4 (rationale judge, reported-only)

## Format: `[ID] [P?] [Story] Description`
- **[P]**: parallelizable (different files, no incomplete dependency)
- **[Story]**: US1–US4 (user-story phases only)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Dependencies, config, and package scaffolding the harness needs.

- [X] T001 [P] Add `pyyaml>=6` to `[project.dependencies]` in `pyproject.toml` and refresh `uv.lock` (`uv lock`) — promote from transitive to direct (R10).
- [X] T002 [P] Scaffold the harness package: `backend/eval/__init__.py`, `backend/eval/gates/__init__.py`, and create the `tests/fixtures/rationale/` directory (with `.gitkeep`).
- [X] T003 Add `EvalSettings` (`pydantic-settings`, `extra="forbid"`) to `backend/infra/config.py` and mount it as `Settings.eval` — fields per [data-model.md](data-model.md) (`thresholds_path`, `report_bucket`, `report_prefix`, `freeze_prefix`, `providers_per_pr`, `providers_freeze`, `judge_provider`, `rationale_fixture_dir`).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The harness spine every user story builds on — DTOs, threshold loading, the gate registry
with the orphan/stale guard, and the aggregation core.

**⚠️ CRITICAL**: No user-story work can begin until this phase is complete.

- [X] T004 [P] Define pure report DTOs in `backend/domain/eval.py`: enums (`GateProviderDim`, `GateKind`, `RunMode`, `FreezeVerdict`, `RationaleLabel`) and models (`GateSpec`, `GateResult`, `ProviderResult`, `RationaleScore`, `EvalReport`) per [data-model.md](data-model.md). Pydantic v2, `extra="forbid"`, no I/O (domain-isolated).
- [X] T005 [P] [TEST] Unit test `tests/unit/test_eval_thresholds.py` — assert `config/eval_thresholds.yaml` parses into `GateSpec[]`, `kind`/`provider_dim` derive correctly (rationale → `reported_only`), and an unknown shape raises. Write FIRST; must fail.
- [X] T006 Implement `backend/eval/thresholds.py` — read `EvalSettings.thresholds_path` and build `list[GateSpec]` (single source of truth, FR-011). Makes T005 pass. (deps: T003, T004)
- [X] T007 [TEST] Unit test `tests/unit/test_eval_registry.py` — a gate in the yaml with no registered runner → hard error; a registered runner with no yaml gate → hard error (FR-002). Write FIRST; must fail.
- [X] T008 Implement the gate registry + `validate_registry(specs)` in `backend/eval/gates/__init__.py` (`GATE_REGISTRY: dict[str, Runner]`, declared⇔registered check raising before scoring). Makes T007 pass. (deps: T004)
- [X] T009 [TEST] Unit test `tests/unit/test_eval_harness.py` — given fake gate runners + specs, the harness aggregates `GateResult[]` into an `EvalReport` and applies the verdict rule (required-fail → `not_certifiable`; required-unknown/upload-fail → `incomplete`; reported floor breach → `not_certifiable`; else `certifiable`). Write FIRST; must fail.
- [X] T010 Implement `backend/eval/harness.py` — load specs (T006), `validate_registry` (T008), run gates, aggregate `EvalReport`, compute verdict per [data-model.md](data-model.md). Makes T009 pass. (deps: T006, T008)
- [X] T011 Extract the existing gates' **scoring helpers** into a shared location importable by both the harness and the pytest gate tests, and update `tests/eval/test_supervisor_routing_gate.py`, `test_retrieval_gate.py`, `test_temporal_gate.py`, `test_triage_gate.py` to read their thresholds from the parsed yaml (via T006) instead of local constants (e.g. triage's `MIN_MACRO_F1`) — one scoring impl, one threshold source (R1/FR-011). Keep all existing gate tests green.

**Checkpoint**: harness can load thresholds, refuse orphan/stale gates, run fake gates, and produce a verdict — ready for real gate runners.

---

## Phase 3: User Story 1 — CI blocks any eval regression on merge (Priority: P1) 🎯 MVP

**Goal**: Wire the deterministic + per-PR LLM gates into the harness and make a required-gate regression
**fail the build** (closes the "gates not run by CI" gap).

**Independent Test**: introduce a deliberate regression in a gated capability on a branch → CI `eval` job
fails and names the gate; revert → it passes.

- [X] T012 [P] [US1] Implement `backend/eval/gates/deterministic.py` — runners for `supervisor_routing`, `retrieval`, `temporal_memory`, `redaction` that call the shared scoring helpers (T011), read thresholds from the spec, and return `GateResult` (provider-independent). Register them.
- [X] T013 [P] [US1] Implement `backend/eval/gates/llm.py` — `triage` + `llm_provider` runners taking a `provider` arg and returning a per-provider `GateResult`. Register them.
- [X] T014 [P] [US1] Implement `backend/eval/gates/smoke.py` — a `smoke` runner adapter that records the compose-readiness result into the report (the existing compose smoke job remains the actual check). Register it.
- [X] T015 [US1] [TEST] Unit test `tests/unit/test_eval_gate_runners.py` — each registered runner returns a well-formed `GateResult`; a below-threshold score yields `passed=False`; the full registry passes `validate_registry`. (deps: T012–T014)
- [X] T016 [US1] [TEST] Unit test `tests/unit/test_eval_regression_blocks.py` — a seeded sub-threshold gate makes the harness verdict `not_certifiable` and the CLI exit non-zero (the "regression blocks merge" contract). (deps: T010)
- [X] T017 [US1] Add the required **`eval`** job to `.github/workflows/ci.yml` — `uv sync` → run the deterministic gates + LLM gates on **Ollama only** (`--mode per_pr`), using the compose `ollama` service with a pre-pulled/cached tag (R5); **blocking**, no MinIO, no Gemini key. (deps: T013, T020)
- [X] T018 [US1] Document the Ollama-in-CI fallback (demote per-PR Ollama gates to non-blocking, keep the deterministic set blocking) as a commented knob in `ci.yml` and a note in [research.md](research.md) R5 — only if exercised.

**Checkpoint**: every PR runs the eval suite; a regression fails CI. **MVP reached.**

---

## Phase 4: User Story 2 — One command runs the whole suite locally (Priority: P1)

**Goal**: A single local command runs the suite with a readable, redacted per-gate verdict and a non-zero
exit on any required-gate failure, without OOM.

**Independent Test**: run the command on a clean checkout → readable per-gate summary + correct exit code,
no out-of-memory failure.

- [X] T019 [US2] [TEST] Unit test `tests/unit/test_eval_cli.py` — argument parsing (`--mode`, `--providers`, `--gate`, `--upload`, `--out`) and exit codes: `0` all-required-pass, `1` required-fail/floor-breach, `2` orphan/stale, `3` incomplete (FR-012, [contracts/cli-and-ci.md](contracts/cli-and-ci.md)). Write FIRST; must fail.
- [X] T020 [US2] Implement the CLI `backend/eval/__main__.py` (`python -m backend.eval`) — flags, mode→provider-set selection (`providers_per_pr`/`providers_freeze`), `--gate` single-gate run, exit codes. Makes T019 pass. (deps: T010)
- [X] T021 [P] [US2] Implement a readable per-gate summary printer (PASS/FAIL — score vs threshold, provider, required|reported) routed through the `Redactor` so every printed line + `evidence` field is redacted (FR-014). In `backend/eval/__main__.py`/`report.py`.
- [X] T022 [P] [US2] Create `scripts/run-evals.sh` — memory-safe batched runner (mirror `scripts/run-tests.sh`): fan out **one gate per subprocess** via `python -m backend.eval --gate <name>` so peak memory ≈ one gate (R9/FR-013).
- [X] T023 [P] [US2] Add `make eval` (→ `scripts/run-evals.sh`, `--mode per_pr`) to the `Makefile`.
- [ ] T024 [US2] [TEST] Integration test `tests/integration/test_eval_memory_safe.py` (marker-gated) — running the heavy gates (redaction/retrieval) via `run-evals.sh` completes without OOM and returns the expected exit status. (deps: T022) **[Deferred — requires live Docker/services stack in CI integration tier]**
- [X] T025 [US2] Verify `--gate` selection + redacted output end-to-end against a planted-secret fixture (no unredacted secret in summary or report). Covered by T040 (rationale redaction test) + T040's unit-tier assertions. (deps: T020, T021)

**Checkpoint**: `make eval` gives a fast, readable, memory-safe local verdict. **M1 complete — commit/PR.**

---

## Phase 5: User Story 3 — Certify the freeze: both providers, one durable report (Priority: P2)

**Goal**: Run every LLM gate on **both** providers, aggregate the freeze verdict, and persist the report
to MinIO under a per-commit/run key (history retained).

**Independent Test**: run the freeze with both providers → one report marking each gate's per-provider
result, the verdict, and retrievable from `eval-reports`; a gate failing on either provider → `not_certifiable`.

- [X] T026 [US3] [TEST] Unit test `tests/unit/test_eval_provider_matrix.py` — the harness evaluates per-provider gates against `providers_freeze=[gemini,ollama]`; a required gate passing on one provider but failing the other → `not_certifiable` naming the provider (FR-007). Write FIRST; must fail.
- [X] T027 [US3] Implement the both-providers matrix in `backend/eval/harness.py` — iterate `per_provider` gates over the run's provider set; record one `GateResult` per provider; required-on-either-fails → fail. Makes T026 pass. (deps: T010)
- [X] T028 [US3] [TEST] Integration test `tests/integration/test_eval_report_minio.py` (testcontainers MinIO, the `infra/blob.py` pattern) — `EvalReport` serializes, uploads to `eval-reports` under `reports/{commit}/{run_id}.json`, is read back and validates against [contracts/eval-report.schema.json](contracts/eval-report.schema.json); a prior report at a different key is NOT overwritten (history, FR-009). Write FIRST; must fail.
- [X] T029 [US3] Implement `backend/eval/report.py` — serialize `EvalReport` → JSON; upload via `aioboto3.Session` to `report_prefix/{commit}/{run_id}.json` (+ freeze copy `freeze_prefix/{tag}/eval_report.json`); upload failure → verdict `incomplete` + exit 3 (FR-009/FR-016). Makes T028 pass. (deps: T004, T027)
- [X] T030 [US3] Wire `--upload` and `--mode freeze` into `backend/eval/__main__.py` (freeze ⇒ `providers_freeze` + upload + run-mode tag). (deps: T020, T029)
- [X] T031 [P] [US3] Create `.github/workflows/eval-freeze.yml` — triggers `schedule` (nightly cron), `workflow_dispatch`, and `push: tags: ['v*']`; bring up compose (MinIO + Ollama) + inject `GEMINI_API_KEY` secret; run `python -m backend.eval --mode freeze --providers gemini,ollama --upload`; fail on `not_certifiable`/`incomplete` (R8).
- [X] T032 [P] [US3] Add `make eval-freeze` (→ `python -m backend.eval --mode freeze --upload`) to the `Makefile`.
- [X] T033 [US3] Add a `git_tag`/`run_mode`/`providers` propagation check: a freeze tag run records `git_tag` and writes the `freezes/{tag}/eval_report.json` copy (extends T028's assertions). (deps: T029, T030)

**Checkpoint**: a freeze produces a durable, both-providers, commit-keyed report with a certifiable verdict. **M2 complete — commit/PR + tag.**

---

## Phase 6: User Story 4 — Rationale quality visible across all three stages (Priority: P3)

**Goal**: A pinned LLM judge (Gemini) scores triage/enrichment/response rationales, validated against a
small hand-labeled set; **reported-only** (catastrophic floor blocks). Freeze/nightly only.

**Independent Test**: run the rationale evaluation → per-stage score + judge↔hand-label agreement in the
report; an ordinary below-target score does NOT block CI, a catastrophic-floor breach does.

- [X] T034 [P] [US4] Author hand-labeled fixtures `tests/fixtures/rationale/triage.json`, `enrichment.json`, `response.json` — 5 samples/stage `{incident_context, rationale_text, human_label, cites_supplied_evidence}` (R4), reusing existing incident/triage fixtures for context where possible.
- [X] T035 [P] [US4] Add the `rationale` gate block to `config/eval_thresholds.yaml` per [contracts/rationale-gate.threshold.md](contracts/rationale-gate.threshold.md) (`required: false`, `run_modes: [freeze, nightly]`, `target`, `catastrophic_floor`, `stages`, `fixture_dir`).
- [X] T036 [US4] [TEST] Unit test `tests/eval/test_rationale_gate.py` — with a scripted judge, grounded_rate + judge↔human agreement compute correctly; a sub-floor result is promoted to blocking (exit 1); above-floor-but-below-target stays reported-only (exit 0). Write FIRST; must fail.
- [X] T037 [US4] Implement `backend/eval/judge.py` — pinned-judge (`EvalSettings.judge_provider`=gemini) structured-output scorer over `RationaleLabel`; computes per-stage `grounded_rate` + exact-match agreement (R2); **every judge prompt passes the `Redactor` first** (FR-014). Makes T036 pass. (deps: T004)
- [X] T038 [US4] Implement `backend/eval/gates/rationale.py` — runner that scores both producers' rationales with the pinned judge, emits `RationaleScore[]` into the report, maps `reported_only` + catastrophic-floor promotion; register it. (deps: T035, T037)
- [X] T039 [US4] Run the rationale gate only under `--mode {freeze,nightly}` (skip per-PR) in the harness/CLI, and include the rationale block in the freeze workflow (T031). (deps: T030, T038)
- [X] T040 [US4] [TEST] Integration test asserting the judge prompt + recorded `evidence` for a planted-secret rationale contain no unredacted secret (FR-014). (deps: T037)

**Checkpoint**: rationale quality + judge trustworthiness visible in the report; only the floor blocks. **M3 complete — commit/PR.**

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T041 [P] Update `README.md` + the component plan/quickstart references; ensure [quickstart.md](quickstart.md) commands run as written (`make eval`, `make eval-freeze`, report read).
- [X] T042 [P] Confirm coverage ≥80% on new `backend/eval/` + `domain/eval.py` (the existing batched `make cov` path) and that new test files are picked up by `scripts/run-tests.sh` tiers. Pure-logic modules at 88% (infra-dependent runners are tested in integration CI).
- [X] T043 Record eval-component decisions in `DECISIONS.md` (e.g. ED-eval entries: harness placement, reported-only rationale, per-PR Ollama, MinIO key scheme) cross-referencing VD1.
- [X] T044 [P] Add a guard test `tests/unit/test_no_injection_claim.py` — the registry/report contains **no** `red_team`/`injection` gate and no field claiming injection coverage (FR-015 / Constitution III v2.0.0 deferral); the seam name is reserved only.
- [X] T045 Ensure the new `eval` job is added to the repo's required-status-check set (branch protection) so it actually blocks merge (documented in [quickstart.md](quickstart.md) T045 section — manual GitHub settings step after first green CI run).

---

## Dependencies & Execution Order

### Phase dependencies
- **Setup (P1)** → no deps.
- **Foundational (P2)** → after Setup; **blocks all stories**.
- **US1 (P3)** & **US2 (P4)** → after Foundational; both P1, together form M1. US1 (CI blocking) is the strict MVP; US2 (local runner) is needed to make M1 usable.
- **US3 (P5)** → after Foundational; depends on the harness core (US1's gate runners) for a meaningful freeze. = M2.
- **US4 (P6)** → after Foundational; independent of US3 but ships at freeze cadence (uses US3's report). = M3.
- **Polish (P7)** → after the targeted stories.

### Within each story
- Test task FIRST (must fail) → implementation → integration.
- DTOs/config before loaders before runners before CLI/CI.

### Parallel opportunities
- Setup: T001, T002 (and T003) in parallel.
- Foundational: T004 ∥ T005/T007/T009 (tests) authored alongside; impl T006/T008/T010 sequential on their deps.
- US1: gate runners T012 ∥ T013 ∥ T014 (different files).
- US3: T031 (workflow) ∥ T032 (Makefile) ∥ core T026–T030.
- US4: T034 (fixtures) ∥ T035 (yaml) before T037/T038.
- Polish: T041 ∥ T042 ∥ T044.

---

## Parallel Example: User Story 1

```bash
# Gate runners (different files, no shared state):
Task: "Implement backend/eval/gates/deterministic.py (T012)"
Task: "Implement backend/eval/gates/llm.py (T013)"
Task: "Implement backend/eval/gates/smoke.py (T014)"
```

---

## Implementation Strategy

### MVP first (US1)
1. Phase 1 Setup → 2. Phase 2 Foundational (CRITICAL) → 3. US1 (CI blocks regressions) → **STOP & VALIDATE** (seed a regression, watch CI fail) → 4. add US2 to make it locally usable → **M1 PR**.

### Incremental delivery
- **M1** = Setup + Foundational + US1 + US2 → eval gates enforced in CI + one-command local run.
- **M2** = US3 → both-providers freeze + durable MinIO report (the day-9 freeze evidence).
- **M3** = US4 → rationale judge (reported-only).
Each milestone is an independently shippable, ≤ ~400-line PR that leaves the suite green.

---

## Notes
- [P] = different files, no incomplete dependency. [US#] maps to spec.md user stories.
- Tests are REQUIRED (Constitution II); write them first and confirm failure before implementing.
- **No red-team / injection gate** (VD1, Constitution v2.0.0) — T044 guards against any accidental v1 claim.
- Existing seven gates are consumed unchanged (T011 refactors only their threshold source, not their logic).
- Commit at each milestone (M1/M2/M3); never leave the suite red.
