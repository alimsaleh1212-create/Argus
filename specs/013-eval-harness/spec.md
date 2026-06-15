# Feature Specification: Consolidated Evaluation Harness & CI Gates

**Feature Branch**: `013-eval-harness`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "on next spec (eval) depending on @docs/resources/SOAR_brief.md and @docs/resources/SOAR_Plan.md and @docs/resources/v_2_3_plan.md"

## Overview

This is component **#13 — `SPEC-eval`**, the **T1 day-9 freeze** spec. It is the *consolidation
and finalization* of Sentinel/Argus's evaluation system, not a fresh build. Across components #2–#10
each eval gate was **seeded into the committed thresholds file as its component landed** (smoke,
redaction, supervisor-routing, llm-provider, triage, retrieval, temporal-memory) with matching gate
tests. What is still missing — and what this spec delivers — is the connective tissue the brief and
constitution promise for the freeze:

1. **A single evaluation harness** that reads the committed thresholds as its source of truth, runs
   every declared gate, scores each against its threshold, and emits one structured report.
2. **CI enforcement of the eval gates** — today the gate tests exist but are **not run by CI** (CI
   runs only the unit/integration/e2e tiers); a regression in a gate cannot currently fail a merge.
3. **The both-providers freeze run** — every eval with an LLM dimension passes on **both** configured
   providers before the tier is frozen.
4. **A durable freeze artifact** — the evaluation report persisted to the blob store as the auditable
   evidence that the tier was certified.
5. **The one genuinely new gate** — an LLM-judge **rationale-quality** evaluation (deferred here by
   ED7 and RD6), covering all three reasoning stages, **validated against hand-labels and reported**.

It is explicitly **scoped down by VD1**: the red-team / injection gate is **deferred to #11 (v3b)** and
is **out of scope** for this spec; the harness only reserves the seam so it can plug in later.

## Clarifications

### Session 2026-06-15

- Q: How should the new LLM-judge rationale-quality gate be scoped? →
  A: **All three reasoning stages (triage, enrichment, response), reported-only.** The judge is
  validated against a small hand-labeled set, scores every rationale, and the agreement/score is
  **recorded in the report**; only a catastrophic floor blocks CI (no flaky probabilistic merge gate).
- Q: When should the "must pass on BOTH LLM providers" suite run in CI? →
  A: **Per-PR runs the deterministic gates plus the LLM gates on a single configured provider; the
  full both-providers suite runs at the tagged freeze and on a nightly schedule.** Matches "both
  providers at the day-9 freeze" while keeping per-PR CI fast and cheap.
- Q: Which single provider runs the per-PR LLM gates? →
  A: **The local model (Ollama).** No cloud credential is required (fork-safe) and per-PR cost stays
  zero while still giving real-model signal; the cloud primary (Gemini) is exercised in the
  freeze/nightly both-providers run.
- Q: What judge strategy does the LLM-judge rationale evaluation use? →
  A: **A single pinned judge — the cloud primary — scores rationales produced by both providers,
  and runs only at freeze/nightly.** A constant yardstick keeps "is the rationale good" from being
  confounded with "is the judge weaker on this provider."
- Q: How is the evaluation report retained in the blob store? →
  A: **Keep history by commit/run.** Every freeze/nightly report is stored under a unique
  commit/run key and retained (never overwritten), enabling a "getting better over time" trend and a
  full audit trail.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - CI blocks any eval regression on merge (Priority: P1)

A developer opens a pull request that unknowingly degrades a graded capability — triage F1 drops below
the committed floor, a redaction boundary starts leaking a planted secret, or supervisor routing sends
an incident to the wrong stage. CI runs the unified eval suite, scores each gate against the committed
thresholds, and **fails the build**, naming the gate that regressed and by how much, before the change
can merge.

**Why this priority**: This is the core promise of the component and of Constitution II — "evals gate
CI." Without enforcement, every other piece of the eval system is decorative. The gate tests already
exist; what is missing is that CI never runs them, so today a regression merges silently.

**Independent Test**: Introduce a deliberate regression in a gated capability on a branch; confirm CI
fails on the eval suite and names the offending gate. Revert; confirm CI passes.

**Acceptance Scenarios**:

1. **Given** a branch whose change drops a required gate below its committed threshold, **When** CI
   runs, **Then** the eval suite fails, the build is blocked, and the failing gate, its score, and its
   threshold are reported.
2. **Given** a branch with no eval regression, **When** CI runs, **Then** the eval suite passes and
   does not block the merge.
3. **Given** the committed thresholds file declares a gate, **When** the harness runs, **Then** that
   gate is executed (no declared gate is silently skipped).

---

### User Story 2 - One command runs the whole suite locally with a readable verdict (Priority: P1)

A developer wants to know, before pushing, whether their change keeps every gate green. They run a
single command and get a per-gate pass/fail summary — score vs. threshold, which provider dimension,
required vs. reported-only — that runs within the machine's memory limits and does not require the full
both-providers freeze run.

**Why this priority**: Fast, local, reproducible feedback is what makes the gates a daily discipline
rather than a CI surprise. It must respect the project's memory constraints (heavy gates run isolated).

**Independent Test**: Run the suite command on a clean checkout; confirm a readable per-gate summary
and a non-zero exit code if any required gate fails, without exhausting memory.

**Acceptance Scenarios**:

1. **Given** a clean checkout, **When** the developer runs the eval suite command, **Then** each
   declared gate reports pass/fail with its score, threshold, and dimension in a readable summary.
2. **Given** a required gate fails locally, **When** the run finishes, **Then** the command exits
   non-zero and the summary highlights the failure.
3. **Given** the heavy gates (those loading large models or graph backends), **When** the suite runs on
   a memory-constrained machine, **Then** the run completes without an out-of-memory failure.

---

### User Story 3 - Certify the freeze: both providers, one durable report (Priority: P2)

At the day-9 tier freeze, a reviewer needs auditable proof the tier is certifiable: every gate with an
LLM dimension passed on **both** configured providers, every deterministic gate passed, and the result
is captured in a single report retained in the blob store and tied to the exact commit.

**Why this priority**: The freeze is the go/no-go gate of the layering contract. The report is the
evidence a mentor or auditor inspects; "passes on both providers before a tier is frozen" is a
non-negotiable from Constitution II.

**Independent Test**: Execute the freeze run with both providers configured; confirm a single report is
produced, marks each gate's per-provider result, records the commit, and is retrievable from the blob
store afterward.

**Acceptance Scenarios**:

1. **Given** both providers are configured, **When** the freeze run executes, **Then** every
   LLM-dimension gate is evaluated against each provider and the report records a per-provider result.
2. **Given** a required LLM gate passes on one provider but fails on the other, **When** the freeze run
   evaluates it, **Then** the freeze verdict is "not certifiable" and the report names the provider that
   failed.
3. **Given** a completed freeze run, **When** it finishes, **Then** the report is persisted to the blob
   store, tied to the commit, and retrievable by a reviewer.

---

### User Story 4 - Rationale quality is visible across all three reasoning stages (Priority: P3)

A reviewer wants to know the agents' plain-language rationales are actually grounded — that triage,
enrichment, and response each cite the evidence they were given rather than confabulating. An LLM judge,
first validated against a small hand-labeled reference set, scores every stage's rationale; the scores
and the judge↔human agreement are recorded in the report.

**Why this priority**: Explainability is a headline capstone claim, but LLM-judge scoring is
probabilistic and the hand-labeled set is small — so it is **reported, not a hard merge gate** (only a
catastrophic floor blocks). It earns its place as visible evidence, not as a flaky CI tripwire.

**Independent Test**: Run the rationale evaluation over the three stages' fixtures; confirm the report
records a per-stage rationale score and the judge↔hand-label agreement, and that an ordinary
below-target score does not block CI while a catastrophic-floor breach does.

**Acceptance Scenarios**:

1. **Given** rationale fixtures for triage, enrichment, and response, **When** the rationale evaluation
   runs, **Then** the report records a per-stage rationale-quality score.
2. **Given** the hand-labeled reference set, **When** the judge is validated against it, **Then** the
   report records judge↔human agreement so the judge's trustworthiness is itself visible.
3. **Given** a rationale score below its target but above the catastrophic floor, **When** CI runs,
   **Then** the build is **not** blocked (reported-only); below the floor, it is.

---

### Edge Cases

- **Orphan gate**: a gate is declared in the thresholds file but no evaluation implements it → the
  harness MUST fail loudly (the anti-gap rule), never silently skip.
- **Stale gate**: an evaluation runs but its gate is missing from the thresholds file → flagged, since
  thresholds are the single source of truth.
- **Provider outage at freeze**: a required LLM gate cannot complete on one provider → the freeze is
  "not certifiable" (absence of a result is not a pass); a reported-only dimension records "unknown".
- **Provider disparity**: a required gate passes on the cloud provider but fails on the local one →
  freeze blocked; this is the intended both-providers tension, not a harness bug.
- **Report upload failure**: the report cannot be persisted to the blob store → the freeze run is
  reported as incomplete, because the durable artifact is itself the freeze deliverable.
- **Coverage regression**: new code drops total coverage below the committed floor → the coverage gate
  fails independently of the capability gates.
- **Judge flakiness**: the LLM judge disagrees with hand-labels or itself across runs → tolerated by
  the reported-only design; it never blocks merge except at the catastrophic floor.
- **Red-team probe**: an injection payload in alert text is **not** evaluated in v1 → documented as
  deferred (VD1); no eval result may imply injection coverage exists.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a single evaluation harness entry point that reads the committed
  thresholds file as its source of truth, runs every declared gate, scores each against its threshold,
  and produces one structured result set.
- **FR-002**: The harness MUST adopt the already-seeded gates (smoke, redaction, supervisor-routing,
  llm-provider, triage, retrieval, temporal-memory) **without re-implementing or re-defining** them;
  every gate declared in the thresholds file MUST be executed, and every executed gate MUST correspond
  to a declared threshold (no orphan gate, no un-thresholded gate).
- **FR-003**: The system MUST add a **rationale-quality** evaluation using an LLM judge that covers the
  **triage, enrichment, and response** rationales. The judge MUST be a **single pinned model (the cloud
  primary)** scoring rationales produced by **both** providers, and MUST run only at the freeze/nightly
  cadence. The judge MUST first be validated against a small committed hand-labeled reference set, and
  both the rationale scores and the judge↔human agreement MUST be recorded in the report.
- **FR-004**: The rationale-quality evaluation MUST be **reported-only**: ordinary below-target scores
  MUST NOT block merge; only a committed **catastrophic floor** blocks. The deterministic capability
  gates remain hard (blocking).
- **FR-005**: The system MUST wire the eval suite into CI as an enforced check so that a regression in
  any **required** gate fails the build and blocks merge (closing the current gap where gate tests are
  not run by CI).
- **FR-006**: Per-PR CI MUST run the deterministic / provider-independent gates plus the LLM-dimension
  gates on a **single** configured provider, and that provider MUST be the **local model (Ollama)** — no
  cloud credential required, so fork PRs and routine runs stay fork-safe and zero-cost. The cloud primary
  is exercised in the **full both-providers suite**, which MUST run at the tagged freeze and on a nightly
  schedule.
- **FR-007**: At the freeze (and nightly), each LLM-dimension gate (at minimum triage, llm-provider, and
  rationale) MUST be evaluated against **both** configured providers; a regression on **either** provider
  MUST make a **required** gate fail the freeze.
- **FR-008**: The harness MUST produce a structured evaluation report capturing, per gate: name, score,
  threshold, pass/fail, the provider dimension (or "provider-independent"), required vs. reported-only,
  and an overall freeze verdict (certifiable / not certifiable), tied to the evaluated commit and a
  timestamp.
- **FR-009**: At a freeze run, the report MUST be persisted to the blob store under a **unique
  per-commit/run key and retained as history** (never overwriting prior reports), so earlier reports stay
  retrievable for trend and audit; failure to persist MUST mark the freeze run incomplete.
- **FR-010**: The system MUST consolidate and affirm three-tier test discipline (unit, integration,
  e2e) with the committed coverage floor (≥80% on new code, higher on remediation and the safety
  boundary); the eval suite MUST NOT undermine or bypass the tiering.
- **FR-011**: Gate thresholds MUST live solely in the committed thresholds file and be read from it at
  run time; evaluations MUST NOT hardcode threshold values that can silently diverge from the file.
- **FR-012**: The suite MUST be runnable end-to-end with a single local command that returns a readable
  per-gate pass/fail summary and a non-zero exit status when any required gate fails.
- **FR-013**: The harness MUST run within the project's memory constraints — heavy gates (those loading
  large language/PII models or the graph backend) MUST be isolatable so a full run does not exhaust
  memory.
- **FR-014**: All judge prompts, gate inputs, and report contents MUST pass through the existing
  redaction boundary so the evaluation system itself never emits an unredacted secret (the eval system
  is subject to the same redaction guarantee it verifies).
- **FR-015**: The red-team / injection gate MUST be treated as **out of scope and deferred** (VD1 →
  #11/v3b); the harness MUST reserve a seam for it but MUST NOT claim or imply injection coverage in any
  v1 report. *(The dependent constitution amendment is owned separately — see Dependencies.)*
- **FR-016**: A reported-only or non-required dimension whose provider is unavailable MUST be recorded
  as "unknown" without aborting the overall run; an unavailable provider for a **required** dimension at
  freeze MUST yield "not certifiable" rather than a pass.

### Key Entities

- **Eval Gate**: a named, committed capability check with a description, a `required` flag, a threshold
  (or threshold set), a provider dimension (independent / per-provider), and a test tier. Declared in
  the committed thresholds file; the file is the single source of truth.
- **Gate Result**: the outcome of running one gate — score, threshold, pass/fail, provider, and brief
  evidence — for a given run and commit.
- **Evaluation Report**: the aggregate of all gate results for a run, plus the overall freeze verdict,
  the provider matrix, the commit, and a timestamp; persisted to the blob store at a freeze.
- **Rationale Judge Sample**: a hand-labeled reference rationale (per stage) with its human label,
  against which the LLM judge is validated to produce a reported judge↔human agreement.
- **Golden / Fixture Set**: the committed labeled data backing each gate (e.g. labeled alerts for
  triage, prior incidents for retrieval, temporal scenarios for time-validity), owned by its originating
  component and consumed unchanged here.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of gates declared in the committed thresholds file are executed by the harness, and
  100% of executed gates map to a declared threshold (zero orphan gates, zero un-thresholded gates).
- **SC-002**: A pull request that regresses any required gate is blocked by CI — demonstrated by
  introducing a deliberate regression and observing the build fail and name the gate.
- **SC-003**: At the freeze, every required gate passes on **both** configured providers, evidenced by a
  single persisted report whose overall verdict is "certifiable".
- **SC-004**: The evaluation report is retrievable from the blob store after a freeze run and contains,
  per gate, the score, threshold, pass/fail, provider dimension, and the evaluated commit — and prior
  reports remain retrievable (history is retained by commit/run, not overwritten).
- **SC-005**: A developer runs the entire suite locally with one command and receives a readable per-gate
  verdict, with a non-zero exit on any required-gate failure, without an out-of-memory failure.
- **SC-006**: The report records a rationale-quality score for all three reasoning stages and a
  judge↔hand-label agreement figure for the judge's own validation.
- **SC-007**: Per-PR CI completes the deterministic and single-provider gates within the normal CI time
  budget (fast feedback), with the both-providers suite reserved for the freeze and nightly runs.
- **SC-008**: No v1 evaluation result claims or implies injection / red-team coverage; the report
  documents that gate as deferred.

## Assumptions

- This component **finalizes** rather than rebuilds: the seven seeded gates and their fixtures already
  exist and are consumed unchanged; only the rationale gate is net-new content.
- Both LLM providers (a cloud primary and a local secondary) are configured; the freeze run requires a
  real cloud credential and a running local-model service, while per-PR CI needs only the local model
  (no cloud credential).
- The blob store reserved at platform setup is the destination for the durable report; it is reachable
  in the freeze/nightly environment.
- The hand-labeled rationale reference set is intentionally **small** ("hand-label a few"), consistent
  with the brief; the judge is validated against it before scoring, and results stay reported-only.
- The redaction boundary built earlier is reused wholesale; the eval system adds no de-redaction path.
- The memory-safe batched test-execution pattern already used for the tiers extends to the heavy eval
  gates to avoid out-of-memory failures on constrained machines.
- "Both providers" means the two providers configured today; adding a third later extends the matrix
  without changing the harness contract.
- The smoke gate (fresh-clone bring-up to readiness) is part of the suite/report even though it has no
  LLM or capability dimension.

## Dependencies

- **All capability components #2–#10** (and #12): each owns and seeds its gate(s) and fixtures; this
  spec consumes them and must not redefine them.
- **Platform/infra (#1)** for the blob store that holds the durable report, and the configuration seam.
- **Observability & redaction (#2)** for the redaction boundary every judge prompt and report passes
  through.
- **LLM provider (#3)** for the uniform provider seam the both-providers run and the rationale judge
  call through (no direct vendor calls).
- **Constitution amendment (separate)**: VD1 records the red-team/guardrails deferral; the corresponding
  Constitution III amendment removing the v1 red-team-gate mandate is a `/speckit-constitution` action,
  **not** owned by this spec — this spec only declines to build that gate and reserves its seam.
