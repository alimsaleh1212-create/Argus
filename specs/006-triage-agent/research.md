# Phase 0 — Research & Decisions: Triage Agent

**Component**: #8 `SPEC-triage-agent` · **Date**: 2026-06-08

The spec had **no open `[NEEDS CLARIFICATION]`** markers, so Phase 0 records the design decisions that turn
the spec into a buildable plan. Every decision is biased toward the user's standing steer — *make it simple,
don't overengineer* — and toward Constitution Principle IV (agents reserved for the ambiguous tail; reason
only over supplied evidence; abstain when unsure). Decisions are labeled `TD1…TD8`; the non-obvious ones are
mirrored into `DECISIONS.md`.

---

## TD1 — One structured-output LLM call through the shared adapter; **no agentic loop, no tools**

**Decision**: Triage makes **exactly one** call to the `LlmClient` (#3) with `response_schema` set to the
`TriageJudgment` JSON schema and a system prompt. No tool-calling, no multi-step reasoning loop, no
self-retry inside triage. `tokens_consumed` is taken from `response.usage` (prompt + completion) and
returned in the `StageResult` for the supervisor's cap.

**Rationale**: The spec is explicit (FR-009, SC-006, Out-of-Scope): "v1 is a single bounded reasoning call."
A junior-analyst real/noise/uncertain judgment over already-assembled evidence is a one-shot classification
with a rationale — not an investigation. The adapter already provides timeout, transient-only retry,
provider fallback, fail-closed contract validation, and redaction, so triage adds **zero** orchestration of
its own. This is the smallest thing that can possibly work and exactly the "one bounded agentic step"
lesson the brief cites.

**Alternatives considered**: *Tool-calling triage (let it fetch more context)* — rejected: that is
enrichment's job (#9), needs action/retrieval tools triage must not hold, and breaks the one-call rule. *A
ReAct/multi-step loop* — rejected: unnecessary for a classification, blows the token cap, and adds a failure
surface for no measured accuracy gain. *Free-text completion then regex-parse* — rejected: structured output
+ schema validation is the fail-closed contract the constitution wants.

---

## TD2 — Pure judgment types in a new `domain/triage.py`

**Decision**: Add `domain/triage.py` with `TriageVerdict` (`real` / `noise` / `uncertain`) and the
`TriageJudgment` model (`verdict`, `confidence` ∈ [0,1], `assessed_severity: Severity | None`, `rationale`,
`cited_evidence: list[str]`). It may import `Severity` from `domain/incident.py` (the "Domain is isolated"
import-linter contract forbids only domain→**outward** imports, not domain→domain). It is consumed by the
dashboard (#12) to render the verdict and by the eval; it is **not** added to `domain/pipeline.py` (which is
the cross-stage contract, kept stage-agnostic).

**Rationale**: A small, dependency-light pure module keeps the judgment shape reusable and testable without
pulling in `infra`. Reusing `Severity` keeps `assessed_severity` type-safe and comparable to the canonical
severity (which it never overwrites — TD6 / FR-012). Verdict is a closed enum so an out-of-vocabulary value
fails validation → fail-closed escalate (FR-007).

**Alternatives considered**: *Put types in `pipeline.py`* — rejected: pollutes the generic stage contract
with triage specifics. *Define inline in `agents/triage.py`* — rejected: the dashboard and eval would have
to import from the agent layer (and `domain` can't import `agents`); a domain home is correct and import-safe.

---

## TD3 — Verdict → outcome is a **pure, config-threshold-gated** function (abstain is the default-safe path)

**Decision**: A pure `decide_outcome(judgment, cfg) -> tuple[StageOutcome, str | None]` maps the judgment to
a supervisor outcome using two config-backed thresholds:

| Condition (checked in order) | Outcome | Disposition |
|------------------------------|---------|-------------|
| `verdict == uncertain` | `ESCALATE` | `escalated_triage` |
| `confidence < advance_min_confidence` (any verdict) | `ESCALATE` | `escalated_triage` |
| `verdict == real` and `confidence ≥ advance_min_confidence` | `ADVANCE` | — |
| `verdict == noise` and `confidence ≥ resolve_min_confidence` | `RESOLVED` | `auto_resolved_triage` |
| `verdict == noise` and `advance_min ≤ confidence < resolve_min` | `ESCALATE` | `escalated_triage` |

Boundary is explicit (spec edge case): **at-or-above** the bar passes, **strictly below** abstains.

**Rationale**: Two thresholds, not one, because the outcomes are **asymmetric in blast radius**:
auto-resolving (closing) a *real* incident as noise is the dangerous error; advancing a noise incident only
wastes one enrichment stage (which can still resolve it). So auto-close must clear a **higher** bar
(`resolve_min_confidence`, default `0.7`) than advance (`advance_min_confidence`, default `0.6`). This is
the minimal safe knob set — the spec explicitly says "threshold(s)" and requires abstention below threshold
(FR-004, SC-003). Keeping the mapping a pure function makes it unit-testable independent of the LLM and keeps
all policy out of the prompt (Constitution IV).

**Alternatives considered**: *Single threshold for everything* — rejected: ignores the real/noise asymmetry
and makes auto-close as easy as advance. *Per-severity threshold matrix* — rejected as premature
over-engineering; two scalars cover v1 and the v2c feedback loop (#6) can tune them later. *Thresholds in the
prompt* — rejected: violates FR-004 (config-backed, changeable without touching reasoning) and is not
testable.

---

## TD4 — `TriageSettings` config section (typed, `extra="forbid"`, sensible defaults)

**Decision**: Add a `TriageSettings` block to `config.py` (env `SENTINEL__TRIAGE__*`), register `"triage"`
in `_KNOWN_SENTINEL_SECTIONS`, and add the `triage: TriageSettings` field to `Settings`. Fields:
`advance_min_confidence: float = 0.6`, `resolve_min_confidence: float = 0.7`, `max_output_tokens: int = 512`,
`temperature: float = 0.0`, `prompt_version: str = "v1"`. A `model_validator` enforces
`0 ≤ advance_min ≤ resolve_min ≤ 1`.

**Rationale**: One typed settings object per the constitution; `extra="forbid"` catches typos at boot.
Defaults are chosen so the system is usable out of the box (deterministic `temperature=0.0` for a
classification; a tight output budget since the schema is small). The `resolve ≥ advance` invariant is
enforced once, centrally, so the asymmetry in TD3 can never be misconfigured into "auto-close easier than
advance."

**Alternatives considered**: *Reuse `SupervisorSettings`* — rejected: triage knobs belong to triage and
change for independent reasons. *No `prompt_version`* — rejected: cheap to carry now and lets the eval pin a
prompt; supports later iteration without a code change to the eval contract.

---

## TD5 — DI by **handler-factory closure**; worker registers the LLM provider before the supervisor

**Decision**: Triage exposes `make_triage_handler(llm: LlmClient, cfg: TriageSettings) -> StageHandler` that
returns `async def handler(incident: Incident) -> StageResult`. `SupervisorProvider.build` reads
`container.llm` and `settings.triage` and wires `StageName.TRIAGE: make_triage_handler(llm, cfg)`; the
enrichment/response stubs stay bare functions for now. `worker.py` adds `register_llm_provider()` **before**
`SupervisorProvider` so `container.llm` exists when the supervisor provider builds (provider build order =
registration order; `settings._container` exposes already-built siblings, the pattern `LlmProvider` already
uses).

**Rationale**: The closure injects the `LlmClient` and config **without changing** the frozen `StageHandler`
signature (`Incident` in, `StageResult` out) — so the structural boundary (no session, no action client ever
reaches triage) is preserved exactly (Constitution III, #7 SD4), and tests substitute a fake `LlmClient`
trivially. The worker currently never registered the LLM provider (the supervisor stub made no LLM call);
adding it there is the one wiring change real triage needs.

**Alternatives considered**: *Widen `StageHandler` to take dependencies as args* — rejected: breaks the
frozen seam #9/#10 also fill and leaks DI into the supervisor's call site. *Build the `LlmClient` inside
triage* — rejected: violates lifespan-singleton DI, defeats mocking, and re-resolves the Vault key per call.
*A global/module singleton* — rejected: untestable, not disposed on shutdown.

---

## TD6 — Reason only over supplied, already-redacted evidence; record assessed severity without overwriting

**Decision**: The user message is a bounded, structured serialization of the incident's **evidence slice**
only: `verdict`, `severity`, the `normalized_event` fields, `summary`, and `retrieved_context` (typically
empty in v1). The system prompt instructs the model to (a) judge **real / noise / uncertain** using *only*
the supplied evidence, never background knowledge of whether the indicator is "really" malicious; (b) cite
≥1 specific evidence item in the rationale; (c) answer `uncertain` when the evidence is insufficient. Triage
**may** return an `assessed_severity` that differs from the ingested severity, recorded in the judgment, but
**never** rewrites the canonical `incidents.severity` (FR-012).

**Rationale**: This is the FR-005 contract and the brief's "synthesis-and-judgment, not re-detection"
framing. Evidence is already redacted at grounding (#4) and the adapter additionally credential-scrubs the
outbound prompt and redacts span previews (#2/#3), so triage emits no raw alert content to logs/traces
(FR-011). Preserving canonical severity keeps provenance intact and the supervisor the single writer.

**Alternatives considered**: *Pass the raw alert* — rejected: defeats redaction and invites the model to
re-detect from priors. *Let triage overwrite severity* — rejected: violates single-writer + provenance;
`assessed_severity` is advisory evidence the dashboard/enrichment can read.

---

## TD7 — Fail-closed error mapping: `LlmError`/validation failure → `ToolError`/escalate, never crash

**Decision**: Triage maps the adapter's `LlmError` and its own validation failures onto the supervisor's
`ToolError`/outcome contract:

| Cause | Triage action |
|-------|---------------|
| `LlmError(TRANSIENT)` (timeout, 5xx) | `raise ToolError(retryable=True, kind="llm_transient")` → supervisor retries within `max_stage_retries`, then escalates |
| `LlmError(EXHAUSTED)` (both providers failed transiently) | `raise ToolError(retryable=True, kind="llm_exhausted")` → one more supervisor retry, then escalates |
| `LlmError(AUTH / INVALID_REQUEST / CONTENT_REFUSAL)` | `raise ToolError(retryable=False, kind=…)` → escalate immediately |
| `LlmError(CONTRACT_UNSATISFIED)` or local Pydantic/enum validation failure (malformed / out-of-vocabulary) | `raise ToolError(retryable=False, kind="malformed_output")` → **escalate (fail-closed)**, never auto-resolve/advance |

Triage never returns `RESOLVED`/`ADVANCE` on any unvalidated or partial output. The supervisor's existing
loop already converts any escaped `ToolError`/exception into an `escalated` transition and keeps the worker
running.

**Rationale**: Directly satisfies FR-007, FR-008, US3, and SC-005. Transient = retryable (the supervisor
owns the retry policy, not triage — TD1). Malformed output is treated as a permanent failure for *this*
attempt and escalated rather than retried, matching the spec's fail-closed wording and keeping behavior
simple and predictable (a stochastic re-roll is not worth the extra call against the cap).

**Alternatives considered**: *Retry malformed output once* — rejected: the spec says malformed → escalate;
re-rolling spends the cap for a non-deterministic maybe. *Auto-resolve on provider failure* — explicitly
forbidden (would silently drop real threats). *Let exceptions propagate uncaught* — rejected: the supervisor
catches them, but mapping to a typed `ToolError` gives clean disposition reasons and honors the contract.

---

## TD8 — Supervisor persists triage's `evidence_patch` (the one spec-scoped state-machine extension)

**Decision**: Triage returns `StageResult(stage=TRIAGE, outcome=…, tokens_consumed=…, disposition=…,
evidence_patch={"triage": judgment.model_dump(mode="json")}, note=<redacted rationale preview>)`. The
supervisor passes `result.evidence_patch` to `repo.advance_status(...)`, which gains an optional
`evidence_patch: dict | None` parameter and JSONB-merges it into the existing `evidence` column in the **same
guarded transition** (`evidence = COALESCE(evidence,'{}'::jsonb) || :patch::jsonb`). The supervisor remains
the **single writer**; triage still writes nothing.

**Rationale**: FR-010 + the spec's Assumptions/Out-of-Scope explicitly scope this: "a small extension to the
supervisor's existing transition step" to persist triage's patch, and nothing more in the transition table.
A JSONB merge in the guarded `UPDATE` keeps it atomic and single-writer, so the dashboard (#12) and
downstream stages can read `evidence.triage`. Reusing the proven `advance_status` guard means no new
concurrency surface.

**Alternatives considered**: *A separate `judgments` table* — rejected: premature; `evidence` JSONB is the
established home for stage outputs and the dashboard already reads it. *Let triage write its own slice* —
rejected: violates single-writer + the structural no-write boundary. *Stuff the judgment into `disposition`*
— rejected: `disposition` is the coarse outcome reason, not the structured judgment.

---

## Resolved unknowns summary

| Question | Resolution |
|----------|------------|
| One call or a loop/tools? | **One** structured-output call via the #3 adapter; no tools, no loop (TD1). |
| Where do judgment types live? | New pure `domain/triage.py` (`TriageVerdict`, `TriageJudgment`); reuses `Severity` (TD2). |
| How does verdict become an outcome? | Pure `decide_outcome`, two config thresholds, abstain-by-default (TD3). |
| Config knobs? | `TriageSettings` (`advance_min`/`resolve_min`/`max_output_tokens`/`temperature`/`prompt_version`), `resolve ≥ advance` enforced (TD4). |
| How is the `LlmClient` injected? | `make_triage_handler(llm, cfg)` closure; worker registers `LlmProvider` before `SupervisorProvider` (TD5). |
| What does triage reason over? | Only the redacted evidence slice; never priors; records but never overwrites severity (TD6). |
| Failure behavior? | Fail-closed: transient→retryable `ToolError`; malformed/permanent→escalate; worker never crashes (TD7). |
| How is the judgment persisted? | Supervisor JSONB-merges `evidence_patch` via an extended `advance_status` — single writer (TD8). |
| Eval gate? | Triage macro-F1 gate on a committed labeled set, run on **both** providers (see `contracts/triage-eval.md`). |
| New dependency / service / migration? | **None** — runs in the existing worker; reuses the `evidence` column. |
