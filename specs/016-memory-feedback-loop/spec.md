# Feature Specification: Memory Feedback Loop (Gets Smarter Over Time)

**Feature Branch**: `016-memory-feedback-loop`

**Created**: 2026-06-16

**Status**: Draft

**Input**: User description: "Specify the next v2 component from the prioritized roadmap. Source of intent: `docs/resources/SOAR_brief.md` and `docs/resources/v_2_3_plan.md`. Per the value-early sequence (`T1 freeze → 015-M1 → 016-M1 → …`), with `015-remediation-verification` merged, the next component is `016-memory-feedback-loop`: make the system *get smarter over time* — write each verification verdict and remediation outcome back to temporal memory as a time-valid fact, and let future incidents be handled differently because of it (within-SOAR tuning, memory/retrieval not retraining)."

---

## Why This Feature (Context)

v1 *fires and records*. `015-remediation-verification` added the closed-loop **verdict** — after any remediation
the system now knows whether the threat was actually `verified` / `unverified` / `regressed`. But today that
verdict dies with the incident: it is recorded on the incident's evidence and (per #15) is **not yet written
back to memory**. The next incident on the same indicator starts from zero, so the system never *learns* that a
remediation it applied last week did not hold.

This feature closes that gap — the "gets smarter over time" capability that distinguishes the brief. Critically,
this is **memory/retrieval, not model retraining** (Constitution VI): nothing is trained at runtime; the system
accumulates an explicit, **time-valid** record of what was decided and what actually worked, then retrieves it to
inform future reasoning. It ships in two milestones, sequenced value-early so the riskiest dependency (the
detector, #14) is not on the path to the first demo:

- **M1 — within-SOAR tuning (no detector).** Every verification verdict + remediation outcome is written back to
  temporal memory as a **time-stamped fact** (invalidate-not-delete preserves "*worked* as of Monday,
  *regressed* as of Friday"). Future incidents consume it through retrieval that **already exists**: triage and
  enrichment read the indicator/entity's prior dispositions and verdicts; severity and routing bias toward
  escalation on a prior `regressed`; playbook selection prefers a **stronger** playbook on a known-failed
  remediation. **This is the buildable, demoable scope today — the brief's "same alert handled differently after
  memory accumulates" demo.**
- **M2 — feed-to-detector (gated on #14 detector).** Memory-derived intel — confirmed-malicious indicators,
  recurrence patterns, which remediations actually held — is exported as detection signals / threshold tuning
  into the detector (#14). **This is the headline "closes the detection↔response loop that defines a mature SOC,"
  documented here as a marked, gated milestone — built only after #14 lands.**

This spec **reuses existing seams** rather than inventing them: the temporal-memory write path (#6 — the same
off-path, best-effort, post-terminal memory write the worker already performs for episodes) and the existing
retrieval paths that triage/enrichment already call (the time-valid fact query and similar-incident search). The
verdict it writes is the one `015-remediation-verification` produces. Contract churn is therefore minimal: a new
time-valid **outcome fact**, deterministic **bias rules** on the read-side consumers, and a new
**feedback-effectiveness** eval gate.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The system records what actually worked (M1, write-back) (Priority: P1)

When an incident reaches a terminal disposition carrying a verification verdict (from #15), the system writes the
verdict and remediation outcome back to temporal memory as a **time-stamped fact keyed to the affected
indicator/entity** — off the disposition path and best-effort, exactly like the existing post-terminal episode
write. When a later incident produces a contradicting outcome for the same indicator (e.g. `verified` last week,
`regressed` this week), the prior fact is **invalidated, not deleted**, so the memory answers "what was true
when," not merely "what is true now."

**Why this priority**: This is the foundation — nothing downstream can get smarter until the outcome is durably,
queryably recorded with its time dimension preserved. It is fully deliverable **without** the detector (#14) and
independently testable. It writes through the established memory seam, so it introduces no new write authority
over incident state (Constitution III — the supervisor remains the only writer of the incident).

**Independent Test**: Drive incidents to terminal with each verdict and assert a time-valid outcome fact appears
in memory for the affected indicator; then drive a second, contradicting outcome for the same indicator and
assert the prior fact is preserved as **superseded** (queryable as-of its original window) while the current
query returns the new state. With memory unavailable, assert the disposition is unaffected (best-effort, no
block) and the write is simply skipped.

**Acceptance Scenarios**:

1. **Given** a terminal incident whose verification verdict is `regressed` on indicator X, **When** the incident
   finalizes, **Then** a time-stamped outcome fact for X recording the verdict and remediation outcome is written
   to temporal memory (off-path, best-effort).
2. **Given** indicator X already carries a `verified` outcome fact and a later incident on X verdicts
   `regressed`, **When** the new fact is written, **Then** the prior `verified` fact is **invalidated, not
   deleted** — a query *as-of* the earlier window returns `verified`, a query *as-of now* returns `regressed`.
3. **Given** temporal memory is unavailable (store down / `NullMemory`), **When** an incident finalizes, **Then**
   the write-back is skipped best-effort and the incident's disposition and terminal state are unaffected.
4. **Given** the same terminal incident is re-finalized on a worker resume, **When** write-back re-runs, **Then**
   it produces no duplicate or conflicting fact for that incident (idempotent).
5. **Given** an incident with no re-checkable indicator/entity, **When** it finalizes, **Then** no outcome fact
   is written and no error is raised (best-effort, nothing to key on).
6. **Given** every outcome fact written, **When** it is persisted, **Then** all entity/indicator values have
   passed redaction before the memory write (Constitution III — redaction before egress).

---

### User Story 2 - The same alert is handled differently after memory accumulates (M1, consumption) (Priority: P1)

Because prior outcomes are now in memory, a *second* incident on an indicator the system has seen before is
handled differently from the first — **deterministically and explainably**. If the indicator previously
`regressed` (a remediation that did not hold), severity and routing bias toward escalation, and the response
stage prefers a **stronger** playbook than the one that previously failed, rather than blindly repeating it. The
change is driven entirely by retrieved, time-valid memory facts (Constitution VI) using config-backed rules
(Constitution IV) — never runtime model training, and never a second writer of incident state.

**Why this priority**: This is the headline value and the brief's demo #5 ("the same alert handled differently
*after* memory accumulates — the feedback loop visibly closing the SOC cycle"). It is the payoff of User Story 1
and, together with it, forms the MVP of "gets smarter over time." It is fully deliverable without the detector.

**Independent Test**: Seed memory with a prior `regressed` outcome fact for indicator X; run a fresh incident on
X and compare against a control incident on an indicator with no prior fact. Assert the X incident is escalated
sooner / carries a higher effective severity and that response selects a stronger playbook — with the difference
attributable to the retrieved fact, and the behavior reproducible (deterministic) across runs.

**Acceptance Scenarios**:

1. **Given** indicator X carries a current `regressed` outcome fact, **When** a new incident on X is routed,
   **Then** severity/routing bias toward escalation relative to an otherwise-identical incident with no prior
   fact (handled more conservatively the second time).
2. **Given** a prior remediation on indicator X is recorded as `regressed`/`unverified` (known-failed), **When**
   the response stage selects a playbook for a new incident on X, **Then** it prefers a stronger playbook than
   the one that previously failed, rather than repeating it.
3. **Given** indicator X carries only a `verified` outcome fact (the remediation held), **When** a new incident
   on X is handled, **Then** no escalation bias is applied on account of that fact (a confirmed success does not
   inflate severity).
4. **Given** the retrieved outcome fact is **superseded** (its validity window has closed), **When** the
   consumers read memory, **Then** they bias on the **current** time-valid state, not a stale superseded one.
5. **Given** the bias rules are applied, **When** the supervisor records the incident's transitions, **Then** the
   incident state is written **only** by the supervisor — feedback tunes the *inputs* (severity, routing,
   playbook choice), it does not introduce a second writer (Constitution III).

---

### User Story 3 - The analyst sees the loop working (read-only dashboard surface) (Priority: P2)

A SOC analyst can see that memory is influencing decisions: a **feedback / memory-hit KPI** (how often a prior
outcome fact informed handling), and, in the incident trace, that a decision was influenced by a retrieved prior
disposition/verdict — with all sensitive values redacted. This makes the "gets smarter" claim visible and
auditable rather than invisible plumbing.

**Why this priority**: The loop is only credible if a human can see it close. The surface is **read-only**
(Constitution III — the supervisor stays the single writer; approve/reject stays the only write path), so it adds
no new write authority. It depends on the verdict/outcome data User Stories 1–2 produce.

**Independent Test**: Drive a population of incidents including repeat indicators that triggered feedback bias,
then assert the dashboard read DTOs expose a feedback/memory-hit KPI and surface, in the trace, that a prior
outcome fact informed handling — with no secret appearing unredacted in any view.

**Acceptance Scenarios**:

1. **Given** a population of dispositioned incidents in which some were influenced by a retrieved prior outcome,
   **When** the analyst views KPIs, **Then** a feedback / memory-hit breakdown is presented (how often memory
   informed handling).
2. **Given** an incident whose handling was biased by a prior outcome fact, **When** the analyst opens its trace,
   **Then** the trace shows (in redacted form) that a prior disposition/verdict informed the decision.
3. **Given** any feedback-related view, **When** it is rendered, **Then** no sensitive value appears unredacted.

---

### User Story 4 - Memory feeds the detector (M2, gated on detector #14) (Priority: P3)

Once the detector (#14) exists, memory-derived intel — confirmed-malicious indicators, recurrence patterns, and
which remediations actually held — is **exported** to the detector as detection signals / threshold tuning,
closing the full detection↔response loop. The detector consumes this export through a defined contract; memory
remains the source, the detector remains downstream.

**Why this priority**: This is the mature-SOC headline, but it is **gated on the detector (#14)**, which is built
later in the sequence — so it is documented here as a marked milestone and built only once #14 lands. M1 already
delivers a complete, honest within-SOAR loop without it. Per Constitution I, no buildable requirement in this
spec depends on #14; M2's requirements are explicitly deferred-until-#14.

**Independent Test** *(deferred until #14 exists)*: With the detector available, accumulate confirmed-malicious
and `regressed` outcome facts, run the export, and assert the detector receives the derived signals / tuned
thresholds through the defined contract and subsequently fires on a matching replayed event end-to-end.

**Acceptance Scenarios** *(M2 — gated on #14)*:

1. **Given** accumulated memory facts (confirmed-malicious indicators, recurrence patterns, held/failed
   remediations) and the detector available, **When** the export runs, **Then** the detector receives
   memory-derived detection signals / threshold tuning through the defined export contract.
2. **Given** an indicator confirmed-malicious in memory, **When** a matching event is later replayed, **Then**
   the detector fires an alert that runs end-to-end through ingestion (the closed loop), with no downstream
   contract change (it emits the existing incident schema).
3. **Given** memory-sourced text is exported to the detector, **When** it is consumed, **Then** it passes the
   same guardrails as alert text (Constitution III tiering — guardrails land by v3b, before any untrusted feed).

---

### Edge Cases

- **Verdict but no indicator to key on** — an incident verified via the executor probe alone (e.g. an
  indicator-less remediation) has no indicator/entity to write a fact against; the write-back is skipped
  best-effort, and no error is raised.
- **Memory write fails mid-finalization** — treated like the existing episode-write failure: logged, swallowed,
  never blocks the disposition acknowledgement (best-effort, off-path).
- **Conflicting current facts for one indicator** — when the consumers must bias on an indicator with multiple
  competing signals, the **worse** outcome dominates (`regressed` > `unverified` > `verified`), so one
  unconfirmed/failed prior prevents an over-confident "handle it the same as last time."
- **Superseded vs current** — bias rules read the **current** time-valid state; a closed (superseded) outcome
  window never drives current handling, but remains queryable for audit/history.
- **No prior fact** — the first incident on an indicator has no feedback to consume and is handled exactly as v1
  would; feedback bias is strictly additive on repeats.
- **Repeat within the same incident lifecycle** — re-finalization on resume must not double-write a fact or
  double-apply bias (idempotent).
- **Confirmed-success indicator** — a `verified` (remediation held) fact must **not** inflate severity on the
  next sighting; only failure-class outcomes (`regressed`/`unverified`) drive escalation bias.
- **(M2) Stale export** — an export of memory-derived intel that has since been superseded must reflect the
  current time-valid state, not a stale snapshot.

## Requirements *(mandatory)*

### Functional Requirements

**M1 — within-SOAR tuning (buildable now)**

- **FR-001**: When an incident reaches a terminal disposition carrying a verification verdict (produced by #15),
  the system MUST write the verdict and remediation outcome back to temporal memory as a **time-stamped fact**
  keyed to the affected indicator/entity, via the existing memory write path — **off the disposition path and
  best-effort** (the same seam as the existing post-terminal episode write).
- **FR-002**: The outcome fact MUST preserve **time-validity** (Constitution VI): when a later incident records a
  contradicting outcome for the same indicator, the prior fact MUST be **invalidated, not deleted**, so a query
  *as-of* a past time returns the historical state and a query *as-of now* returns the current state.
- **FR-003**: Write-back MUST be **best-effort and non-blocking**: a memory outage or write error MUST NOT
  prevent the incident from reaching its terminal/escalated state and MUST NOT change its disposition (it degrades
  to no-op memory behavior, mirroring the existing graceful-degradation contract).
- **FR-004**: Write-back MUST be **idempotent** on worker resume — re-finalizing the same incident MUST NOT
  create duplicate or conflicting outcome facts.
- **FR-005**: All entity/indicator values written as facts MUST pass **redaction before egress** to the memory
  store (Constitution III), reusing the existing memory-write redaction boundary.
- **FR-006**: Future incidents MUST be able to **consume** the accumulated outcome facts through the **existing
  retrieval paths** (the temporal-memory fact query and similar-incident retrieval that triage/enrichment already
  call) — no new retrieval mechanism is introduced for M1.
- **FR-007**: When an indicator carries a current **failure-class** outcome (`regressed` or `unverified`), the
  system MUST bias **severity and routing toward escalation** for a new incident on that indicator, relative to
  an otherwise-identical incident with no such prior fact.
- **FR-008**: When a prior remediation on an indicator is recorded as **known-failed** (`regressed`/`unverified`),
  the response stage's playbook selection MUST prefer a **stronger** playbook than the one that previously failed,
  rather than repeating the failed remediation.
- **FR-009**: A current **`verified`** outcome (the remediation held) MUST NOT inflate severity or routing on the
  next sighting of that indicator — only failure-class outcomes drive escalation bias.
- **FR-010**: All feedback tuning MUST be **deterministic and config-backed** (Constitution IV): the bias rules
  (which outcome classes escalate, the stronger-playbook ordering, any thresholds) are config values, not LLM
  decisions and not hardcoded in stage logic.
- **FR-011**: Feedback MUST tune **inputs only** and MUST NOT introduce a second writer of incident state: the
  supervisor remains the **single writer** of disposition/status (Constitution III); approve/reject remains the
  only human write path.
- **FR-012**: Consumers MUST bias on the **current time-valid** state of an indicator's outcome; a **superseded**
  (closed-window) fact MUST NOT drive current handling.
- **FR-013**: The system MUST expose, read-only, that memory influenced handling — a **feedback / memory-hit**
  signal surfaced in the dashboard KPIs and an indication in the incident trace that a prior disposition/verdict
  informed the decision — all redacted (Constitution III). No new write authority is added by the surface.

**M2 — feed-to-detector (gated on #14; deferred until the detector exists)**

- **FR-014**: Memory-derived intel — confirmed-malicious indicators, recurrence patterns, and which remediations
  actually held — MUST be **exportable to the detector (#14)** as detection signals / threshold tuning through a
  **defined export contract**, with the detector remaining downstream (it emits the existing ingestion schema, no
  new downstream contract).
- **FR-015**: The export MUST reflect the **current time-valid** state of memory at export time (no stale
  snapshot of superseded facts).
- **FR-016**: Memory-sourced text exported to the detector MUST pass the **same guardrails as alert text**
  (Constitution III tiering — those guardrails land by v3b and MUST precede any untrusted live-feed ingestion).

### Key Entities

- **Remediation outcome fact** — a **time-valid** fact keyed to an indicator/entity recording the verification
  verdict (`verified` / `unverified` / `regressed`) and remediation outcome, written via the existing temporal
  memory write path with invalidate-not-delete semantics. Distinct from the existing per-incident **episode**
  (which is searched by similarity): the outcome fact is **queried by entity + time-validity** so a future
  incident can ask "what happened last time on this indicator, and is that still true?"
- **Feedback bias rule** — a deterministic, config-backed rule that maps a retrieved current outcome to a change
  in a pipeline **input**: failure-class outcome → escalation bias on severity/routing; known-failed remediation
  → stronger-playbook preference. Never an LLM decision; never a writer of incident state.
- **Verification verdict** — produced by `015-remediation-verification`; **consumed** here, not produced. #16
  writes it back to memory and tunes on it.
- **Feedback / memory-hit signal** — the read-only indication (KPI + trace) that a prior outcome fact informed an
  incident's handling.
- **Detector export contract (M2)** — the defined interface by which memory-derived intel reaches the detector
  (#14); introduced only with M2.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A committed **feedback-effectiveness** eval proves behavior changes after memory accumulates — on a
  labeled scenario set, the 2nd occurrence of a `regressed`/known-failed indicator is escalated sooner and/or
  selects a stronger playbook than the 1st occurrence, at or above the committed threshold. The tuning is
  deterministic (provider-independent like supervisor-routing), and the full eval suite passes on **each
  configured reasoning provider** (Constitution II) before the tier is frozen.
- **SC-002**: When memory is available, 100% of terminal incidents that carry a verification verdict and have a
  re-checkable indicator produce a corresponding **time-valid outcome fact** in memory.
- **SC-003**: Time-validity is preserved — in 100% of cases where an indicator's outcome changes, the prior fact
  is **invalidated, not deleted**; a query as-of the prior window returns the historical state and a query as-of
  now returns the current state (extends the temporal-memory gate to cover the outcome fact).
- **SC-004**: Behavior change is measurable and attributable — a repeat incident on a failure-class indicator is
  handled more conservatively (higher effective severity / escalation / stronger playbook) than the first
  occurrence, with the difference attributable to the retrieved fact and reproducible across runs.
- **SC-005**: Write-back never blocks disposition — in 100% of runs where memory is unavailable or the write
  fails, the incident still reaches a terminal/escalated state, with no measurable regression to median
  time-to-disposition.
- **SC-006**: Re-running finalization on an already-recorded incident produces **zero duplicate or conflicting
  outcome facts** (idempotence) across repeated worker resumes.
- **SC-007**: The supervisor remains the **single writer** of incident state — feedback introduces no second
  writer of disposition/status; this is verifiable in the design and tests.
- **SC-008**: An analyst can see a **feedback / memory-hit KPI** and, in the trace, that a prior outcome informed
  handling — with **no secret appearing unredacted** in any of these views.
- **SC-009** *(M2, gated on #14)*: Memory-derived intel reaches the detector through the defined export contract,
  and a confirmed-malicious indicator subsequently produces a detector-fired alert that runs end-to-end — closing
  the detection↔response loop.

## Assumptions

- **Build scope today is M1 (within-SOAR tuning).** M2 (feed-to-detector) is **gated on the detector (#14)** and
  is documented here as a marked, deferred milestone; no buildable M1 requirement depends on #14, honoring
  Constitution I (no spec is invalid pending a later spec).
- **Memory/retrieval, not retraining (Constitution VI).** The "gets smarter" capability is institutional memory
  made queryable; **no model is trained at runtime** — out of scope entirely.
- **Reuses existing seams, does not invent them.** Write-back uses the established temporal-memory write path
  (#6; the same off-path, best-effort, post-terminal write the worker already performs for episodes) and
  consumption uses the retrieval triage/enrichment already perform (the time-valid fact query and similar-incident
  search). The verdict written back is the one **#15** produces.
- **Deterministic tuning of inputs (Constitution IV).** Bias rules are config-backed and deterministic; the
  feedback-effectiveness eval is correspondingly deterministic.
- **Single-writer preserved (Constitution III).** Feedback tunes *inputs* (severity, routing, playbook choice);
  the supervisor remains the only writer of incident state and approve/reject the only human write path.
- **Redaction before egress (Constitution III).** Every memory write passes the existing memory-write redaction
  boundary; verification reads/writes redacted state and never de-redacts.
- **Guardrails tiering (Constitution III / VI).** M1 introduces **no new untrusted feed**. M2's memory→detector
  export and any later live feeds must pass the same guardrails as alert text — those guardrails land by **v3b**
  (#11) and MUST precede any **v3c** live-feed ingestion; this is an ordering constraint, not M1 work.
- **Mock-environment honesty (roadmap §6.4).** Outcomes recorded reflect the verification verdicts produced
  against the mock environment (#15); the within-SOAR loop is real, the real-EDR/detector integration is
  contract-shaped but not wired. No spec text implies real-world efficacy.
- **Config-backed values** — which outcome classes drive escalation, the stronger-playbook ordering, any
  thresholds, and the M2 export shape — are fixed during `/speckit-plan` (roadmap §6.3), not here.
- **Layering-contract watch-item.** This is **T2/v2** work. Per roadmap §6.1 the *design* may proceed ahead of
  the T1 tag (additive, low-risk), but **implementation code lands only after the T1 freeze** (#12 dashboard,
  #13 eval green-and-tagged) or under a recorded `DECISIONS.md` entry — a sequencing gate, not a principle
  violation.
- **Dependencies**: #15 (produces the verification verdict consumed/written here); the temporal-memory layer
  (#6 — write path + time-valid fact query); the read-side consumers it tunes — triage/enrichment retrieval,
  supervisor routing/severity, and the response (#10) playbook selector; M2 additionally on #14 (detector).

## Out of Scope

- **Model retraining of any kind** — the "gets smarter" capability is memory/retrieval only.
- **A second writer of incident state** — the supervisor remains the single writer; feedback only tunes inputs
  (Constitution III).
- **Producing the verification verdict** — that is **#15**; #16 only consumes and writes it back.
- **The detector itself (#14)** and the **M2 export wiring** — deferred until #14 lands.
- **Live network capture / live-feed ingestion (v3c)** and the **guardrails library (v3b / #11)** — out of scope
  here; the guardrails-before-feeds ordering constraint is noted only.
- **De-redaction** of any memory-read or memory-write state.
- **New ingestion or downstream contracts** — M2 keeps the detector emitting the existing incident schema; M1
  adds no new schema beyond the time-valid outcome fact on the existing memory store.
