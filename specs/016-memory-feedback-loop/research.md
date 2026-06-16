# Phase 0 — Research: Memory Feedback Loop (#16)

No `NEEDS CLARIFICATION` remained from the spec — the prioritized roadmap (`v_2_3_plan.md` §3 016), the
constitution, and the existing memory/response/grounding contracts pin the design. This file records the design
decisions and the alternatives rejected, in the repo's `Decision / Rationale / Alternatives` format. Config-
backed values (bias thresholds, the stronger-playbook ordering, the M2 export shape) are fixed at planning time
per roadmap §6.3 and captured in [data-model.md](data-model.md).

---

## D1 — Where the write-back runs

**Decision.** Extend the worker's **existing best-effort, off-path, post-terminal write seam**
(`worker._maybe_record_episode` → `services/memory`). Alongside the existing `record_episode`, add
`record_outcome_facts(incident, store, redactor)` that, for a **terminal** incident carrying a verification
verdict (`evidence["response"]["verification"]`), writes one time-valid `TemporalFact` per **applied** action
target via `MemoryStore.write_fact`. Fire-and-forget, wrapped in the same try/except — never blocks or raises
into the disposition path. **No supervisor change.**

**Rationale.** The seam already exists and already has the exact properties #16 needs: off the synchronous
incident path, best-effort, terminal-only, redacted. Reusing it means #16 adds **no new write authority** over
incident state (Constitution III) — it writes only to the memory store, exactly as the episode write does. The
verdict and applied targets are already in `evidence` (produced by #15), so no new plumbing into the response
stage is required.

**Alternatives rejected.** (a) Writing the fact inside the response handler at verdict time — would couple the
write to the synchronous stage and risk blocking disposition; the off-path worker hook is the established
graceful-degradation pattern. (b) A new supervisor post-terminal hook — the supervisor is a pure FSM and the
single writer of *incident* state; the memory write belongs off-path.

---

## D2 — Outcome-fact shape and the key-consistency invariant

**Decision.** A new `fact_type = "remediation_outcome"`; `value =` the verdict string
(`verified`/`unverified`/`regressed`); `entity =` the applied action's target, **keyed identically to how the
reputation fact is keyed today** (`infra/intel.py::_persist_fact` writes `EntityRef(kind, value=indicator)` and
the consumers read it back with the same value). `valid_from =` the incident's terminal `observed_at`. The
store's `write_fact` provides **invalidate-not-delete**: a later contradicting outcome on the same
(entity, fact_type) supersedes the prior one, preserving "worked as of Monday, regressed as of Friday."

**The load-bearing invariant: write-key == read-key.** The feedback loop only closes if the entity value
written equals the entity value the consumers query with. The reputation-fact loop already works this way
(intel writes raw indicator values; `verification._safe_query_fact` and `enrichment/context` read them back
verbatim). #16 therefore **mirrors the reputation keying exactly**, so the new outcome fact is retrievable by
the same `query_fact(entity, ..., as_of=None)` calls. The **feedback-effectiveness eval (D9) is the end-to-end
proof** that the keys agree: if they did not, the 2nd occurrence would not change behavior.

**Redaction.** The fact `value` is a non-sensitive verdict enum. The memory-write redaction boundary
(`Boundary.MEMORY_WRITE`, as used by `record_episode`) still runs on any free-text. Indicator-class keys
(IP/domain/hash) follow the established reputation convention. **Uniform redaction-consistent key normalization
across episode/reputation/outcome facts is a known cross-cutting cleanup — explicitly out of M1 scope** so as
not to destabilize the working reputation loop; noted for a later pass.

**Alternatives rejected.** (a) Reusing the `IncidentEpisode` (which already carries `verdict` + `disposition`)
instead of a fact — episodes are retrieved by *similarity* (`search_similar`), not by *entity + time-validity*;
the feedback loop needs a keyed, time-valid lookup, which is exactly what a `TemporalFact` provides. (b) A new
redacted/pseudonymized key scheme just for outcome facts — would break write-key == read-key against the
existing reputation reads; rejected for M1.

---

## D3 — Where consumption (the feedback lookup) runs

**Decision.** A new read-only `services/feedback.py::gather_feedback(memory, entities, cfg)` that, for the
incident's indicators (the same entity extraction enrichment uses, bounded by `feedback.max_indicators`),
queries `memory.query_fact(entity, "remediation_outcome", as_of=None)` concurrently (`asyncio.gather`,
best-effort `_safe(...)`). It runs **at the grounded boundary in the worker** — *after* `ground()` and *before*
`route_grounded` — so the severity/routing bias (D4) is available before the supervisor routes. The result is a
small `FeedbackSignal` merged into the grounding `Evidence` (a `prior_outcome` slice, redacted) so triage's
rationale can cite it.

**Rationale.** The severity/routing bias **must precede routing**, so it cannot wait for the enrichment stage
(which only runs for ambiguous incidents and after routing). The grounded boundary is the one deterministic,
pre-pipeline point where the worker already assembles and persists evidence (`ground()` → `set_grounded`).
Mirroring the enrichment `_safe(...)` + `gather` pattern keeps it idiomatic, fail-open, and off the LLM path.

**Alternatives rejected.** (a) Doing the lookup inside `ground()` — `grounding.ground()` is a *pure, no-I/O*
function (ID7); adding memory I/O would break that contract. Keep `ground()` pure and run `gather_feedback`
as a separate, clearly-I/O step. (b) Only consuming in enrichment (`context.py`, which already reads facts) —
too late for routing/severity bias; enrichment may not even run on a fast-pathed incident.

---

## D4 — Severity / routing escalation bias (no new FSM edge)

**Decision.** When `gather_feedback` finds a **current** failure-class outcome (`value ∈
feedback.escalate_on`, default `{regressed, unverified}`) for an incident indicator, it applies a config-backed
**severity bias** to the grounded `Evidence` (raise effective severity by one level, or to a configured floor)
and adds a `prior_failure` flag. `route_grounded` already routes on `incident.severity` +
`evidence["flags"]`; the raised severity / flag naturally drives the existing **critical/ambiguous → escalate**
path. **No new `StageOutcome`, no new transition edge** — the bias tunes an *input* the deterministic router
already consumes.

**Rationale.** Constitution III/IV — feedback "tunes inputs," and the supervisor stays a deterministic FSM. The
severity is already a grounding-computed input (`level_to_severity`); biasing it is the least-invasive, fully
deterministic lever and produces the brief's "escalates sooner" behavior with zero FSM churn.

**Alternatives rejected.** (a) A new `StageOutcome`/edge for "prior-failure escalate" — unnecessary churn; the
existing severity→route path already expresses escalation. (b) Letting the triage LLM decide the escalation
from the surfaced prior outcome — non-deterministic, un-eval-able; the bias must be deterministic (the LLM still
*sees* the prior outcome in evidence for its rationale, but does not *decide* the bias).

---

## D5 — Stronger-playbook preference

**Decision.** Add an optional integer **`strength`** to playbook catalog entries (config-backed, in the
playbook yaml; default `0`). In `agents/response/selection.py`, when the action target carries a **current**
failure-class outcome fact and more than one candidate playbook matches (or a higher-`strength` playbook is a
candidate), **prefer the highest-`strength` candidate** rather than the default/first — deterministically,
before any ambiguous-tail LLM call. Gated by `feedback.prefer_stronger_playbook` (default `True`).

**Rationale.** Roadmap: "playbook selection prefers a stronger playbook on a known-failed remediation."
Config-backed `strength` keeps the ordering in config (Constitution VII), not hardcoded in selection logic. It
stays in the **deterministic** match path; when only one playbook matches and no stronger exists, behavior is
unchanged (best-effort).

**Alternatives rejected.** (a) Hardcoding a `{weak→strong}` map in `selection.py` — violates config-backed
(VII). (b) Always escalating to the most destructive playbook on any prior failure — over-aggressive and would
trip the approval interrupt unnecessarily; preferring the *next stronger candidate that already matches the
incident criteria* is the measured choice.

---

## D6 — Settings home

**Decision.** A **new `FeedbackSettings`** section in `infra/config.py` (registered on `Settings`), `extra=
"forbid"`: `enabled: bool = True`, `escalate_on: list[str] = ["regressed","unverified"]`, `severity_bias:
Literal["bump_one","to_critical","none"] = "bump_one"`, `prefer_stronger_playbook: bool = True`,
`max_indicators: int = 5`, `outcome_fact_type: str = "remediation_outcome"`.

**Rationale.** Unlike #15 (a response sub-concern that reused `ResponseSettings`), feedback is genuinely
**cross-cutting** — it spans the write (worker), the grounding/routing bias (supervisor-adjacent), and the
playbook selector (response). A single typed section is the honest home and avoids fragmenting one concern
across three existing sections. `extra="forbid"` + the env-var contract are inherited.

**Alternatives rejected.** Extending `SupervisorSettings`/`ResponseSettings` piecemeal — fragments the concern
and couples unrelated sections; rejected per the Complexity-Tracking note in [plan.md](plan.md).

---

## D7 — Single-writer honesty (Constitution III)

**Decision.** Feedback writes to **two places, neither of which is the supervisor's incident-state transition**:
(1) the **memory store** (the outcome fact — the same store the episode write targets, off-path), and (2) the
**grounding `Evidence`** input (the `prior_outcome` slice + severity bias), persisted by the existing
`set_grounded` grounding write the worker already performs *before* the pipeline runs. The **supervisor remains
the sole writer of `status`/`disposition`** transitions; it merely routes on the biased input.

**Rationale.** This preserves Constitution III literally: "feedback tunes *inputs*, the supervisor remains the
only writer." The grounding-evidence write is a pre-pipeline input assembly (v1 already grounds → sets evidence
+ severity), not a pipeline disposition decision.

**Alternatives rejected.** Mutating disposition/severity from within a stage handler or a second service writing
incident status — would create a second writer; rejected.

---

## D8 — Best-effort & graceful degradation

**Decision.** Both halves fail-open. **Write:** wrapped in the existing off-path try/except — a memory outage
skips the write, never blocks disposition. **Read:** `gather_feedback` uses `_safe(...)`; a memory outage or
absent fact yields **no bias** (baseline v1 behavior). No exception from the feedback path ever reaches the
supervisor's routing decision.

**Rationale.** Constitution VI — memory is never a single point of failure. The 1st-occurrence path (no prior
fact) is exactly the no-bias path, so degradation is identical to "first time we've seen this indicator."

**Alternatives rejected.** Hard-failing routing when memory is unavailable — would make the feedback loop a
liability rather than an enhancement; rejected.

---

## D9 — The feedback-effectiveness eval gate (+ extensions)

**Decision.** A new **`feedback`** gate: a block in `config/eval_thresholds.yaml` **and** a registered runner
`backend/eval/gates/feedback.py`, added in the **same** change (the declared⇔registered orphan/stale check is a
hard error — #13). **Deterministic / provider-independent** (like `supervisor_routing`/`verification`): drives
labeled **baseline-vs-repeat** fixture pairs (`tests/fixtures/feedback/`) through the pure bias rules and
asserts the repeat is escalated sooner and/or selects a stronger playbook than the baseline — proving behavior
changes after memory accumulates. **Extends** (not duplicates): `temporal_memory` gains a
`remediation_outcome_flip` case (outcome-fact time-validity); `supervisor_routing` gains a
`prior_regressed_escalates` fixture; `redaction` already covers the memory-write boundary + dashboard view (no
new boundary).

**Rationale.** Roadmap §3 016: "a small **deterministic** eval *proving* behaviour changes after memory
accumulates." Deterministic → provider-independent and 100%-pass-able like routing. The baseline-vs-repeat delta
is also the end-to-end proof of the D2 key-consistency invariant.

**Alternatives rejected.** An LLM-judge feedback gate — unnecessary because the bias is deterministic; cheaper
and provider-independent as a deterministic gate.

---

## D10 — Idempotency & persistence

**Decision.** **No new table** for M1. The outcome fact lives in the memory store keyed by
(entity, `remediation_outcome`); re-finalizing the same terminal incident re-writes the **same** (entity,
verdict, valid_from) — the store's invalidate-not-delete makes a re-write of an identical current fact a no-op
(it does not create a spurious supersession). The write only runs for **terminal** incidents (the worker hook
already guards on terminal status), so resume re-runs are bounded. The read side is naturally idempotent (a pure
query).

**Rationale.** Mirrors #15/#9's zero-migration posture and the existing episode-write idempotency (keyed by
incident id). Constitution VII — Pydantic at the boundary; the supervisor persists nothing new.

**Alternatives rejected.** A dedicated `feedback_facts` table — unjustified for M1 (the outcome is a small,
entity-keyed time-valid fact already served by the memory store); revisit only if M2 needs indexed export
queries.

---

## D11 — M1 / M2 split and the M2 feed-to-detector export boundary

**Decision.** **M1** (within-SOAR tuning) is built now and is self-contained — no migration, no detector, no
new FSM state. **M2** (feed-to-detector) is **designed-but-deferred, gated on the detector #14**: memory-derived
intel (confirmed-malicious indicators, recurrence patterns, which remediations held) is exported to #14 through
a **defined export contract** (current/time-valid snapshot of the relevant facts → detector config/signal
tuning), with the detector still emitting the **existing ingestion schema** (zero downstream change). The export
text passes the **same guardrails as alert text** (Constitution III tiering — guardrails land by v3b, before any
v3c live feed).

**Rationale.** Constitution I — no buildable M1 requirement may depend on a later spec. The roadmap gates M2 on
#14 and frames it as the "closes the detection↔response loop" headline. Keeping M2 design-only here reserves the
seam without dark, un-testable code (there is no detector to consume the export yet).

**Alternatives rejected.** Building the export now against a stubbed detector — would create an un-exercisable
path (nothing consumes it), violating "tests green every day."
