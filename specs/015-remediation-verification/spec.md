# Feature Specification: Remediation Verification (Closed-Loop)

**Feature Branch**: `015-remediation-verification`

**Created**: 2026-06-15

**Status**: Draft

**Input**: User description: "Specify the next v2 component from the prioritized roadmap. Source of intent: `docs/resources/SOAR_brief.md` and `docs/resources/SOAR_Plan.md`, with `docs/resources/v_2_3_plan.md` taking precedence on any contradiction. Per the value-early sequence (`T1 freeze → 015-M1 → 016-M1 → …`), the next component is `015-remediation-verification`: close the action-applied → threat-eliminated gap with a deterministic post-remediation verification verdict."

---

## Why This Feature (Context)

In v1, the response stage stops at **action-applied** — the executor dispatched `isolate_host` / `block_ip` /
`add_to_watchlist` and the incident is dispositioned `remediated` / `auto_remediated`. But *applied* is not
*effective*: NIST SP 800-61 / SANS PICERL deliberately separate **containment** (the action fired — where v1
stops) from **eradication** (the threat artifact is actually gone) and **recovery** (confirmed back to
known-good *and watched*). A mature SOC never trusts a single signal; it confirms the post-state.

This feature closes that gap. After any remediation, the system computes a **verification verdict** —
`verified` / `unverified` / `regressed` — and **never claims a success it cannot confirm**. It ships in two
milestones, sequenced value-early so the riskiest dependency (the detector, #14) is not on the path to the
first demo:

- **M1 — probe verdict (no detector).** A deterministic step at the tail of the response stage compares
  *observed* vs *expected* post-state from an indicator re-check (a real re-query path) and an executor status
  probe (mock now, real-connector-shaped). **This is the buildable, demoable scope of this spec today.**
- **M2 — monitoring loop (gated on #14 detector).** The remediated incident parks for a configured dwell
  window; a recurrence alert reopens it as `regressed`; a clean window confirms `verified`. **Documented here
  as a marked, gated milestone — built only after #14 lands.**

This spec activates contracts that `010-response-remediation` deliberately **reserved** for exactly this work
(the `VerificationVerdict` states, the `verification` slot on the action result, and the
`remediation_unverified` disposition), so the contract churn is minimal.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Honest post-remediation verdict (M1, probe + indicator re-check) (Priority: P1)

After the response stage applies a remediation — on **either** the auto path or the human-approved path — the
system re-checks whether the threat is actually gone before declaring the incident remediated. It re-queries
the targeted indicator's current time-valid reputation/fact state and probes the executor for the observed
post-state, then assigns a verdict: `verified` (expected post-state observed), `unverified` (no confirmation
either way), or `regressed` (threat persists or reappears). A `verified` incident keeps its remediated
disposition; an `unverified` or `regressed` incident is set to `remediation_unverified` and **escalated to a
human** rather than resolved.

**Why this priority**: This is the MVP and the core honesty guarantee — the system stops claiming success it
cannot confirm. It is fully deliverable **without** the detector (#14), so it lands first and is independently
demoable. Everything else in this spec builds on the verdict this story produces.

**Independent Test**: Replay a labeled set of post-remediation states (indicator-now-clean, indicator-still-
malicious, probe-inconclusive) through the response stage and assert the verdict and resulting disposition for
each: clean → `verified` → `remediated`; still-malicious → `regressed` → `remediation_unverified` + escalated;
inconclusive → `unverified` → `remediation_unverified` + escalated. No detector or live environment required.

**Acceptance Scenarios**:

1. **Given** an auto-remediated incident whose indicator now re-checks as clean and whose executor probe
   reports the expected post-state, **When** verification runs, **Then** the verdict is `verified` and the
   incident keeps disposition `auto_remediated`.
2. **Given** a human-approved remediation whose indicator still re-checks as malicious, **When** verification
   runs, **Then** the verdict is `regressed`, the disposition is set to `remediation_unverified`, and the
   incident escalates rather than resolving.
3. **Given** a remediation whose indicator reputation is unknown and whose executor probe is inconclusive,
   **When** verification runs, **Then** the verdict fails closed to `unverified`, the disposition is
   `remediation_unverified`, and the incident escalates.
4. **Given** the intel/reputation lookup is unavailable and memory retrieval errors, **When** verification
   runs, **Then** the verdict is `unverified` (never `verified`) and the incident still reaches a terminal/
   escalated state — verification failure never blocks disposition.
5. **Given** an incident already carrying a recorded `verified` verdict, **When** the worker resumes and the
   verification step re-runs, **Then** the disposition and records are unchanged (idempotent re-run).
6. **Given** a remediation whose only actions ended `failed` / `not_executed`, **When** the stage completes,
   **Then** verification is skipped (there was no applied action to confirm) and the existing escalation path
   for failed remediation is unchanged.

---

### User Story 2 - Analyst sees the verdict (read-only dashboard surface) (Priority: P2)

A SOC analyst reviewing incidents can see, per incident, the verification verdict and the evidence it
considered (which signals were checked, whether a tiebreak reasoning call was needed), with all sensitive
values redacted. Incidents dispositioned `remediation_unverified` are distinguishable in the live queue, and a
`verified` / `unverified` / `regressed` breakdown appears as a KPI.

**Why this priority**: The verdict is only valuable if a human can see and act on it. This is the surface that
makes the honesty guarantee visible. It is read-only (Constitution III — the supervisor stays the single
writer; approve/reject stays the only write path), so it adds no new write authority.

**Independent Test**: Drive an incident to each verdict, then assert the dashboard read DTOs expose the verdict
and redacted evidence in the trace, surface `remediation_unverified` in the queue, and aggregate the verdict
KPI — with no secret appearing unredacted in any view.

**Acceptance Scenarios**:

1. **Given** a verified incident, **When** the analyst opens its trace, **Then** the verification verdict and
   the redacted signals considered are shown as part of the response stage record.
2. **Given** an incident dispositioned `remediation_unverified`, **When** the analyst views the queue, **Then**
   that incident is visibly distinguished from cleanly `remediated` incidents.
3. **Given** a population of dispositioned incidents, **When** the analyst views KPIs, **Then** a
   `verified` / `unverified` / `regressed` breakdown is presented.

---

### User Story 3 - Monitoring loop reopens recurrences (M2, gated on detector #14) (Priority: P3)

After a remediation, instead of resolving immediately the incident parks in a `verifying` state for a
configured **dwell window**, reusing the existing park/resume machinery (the same mechanism that powers
`awaiting_approval`). If the detector (#14) fires a follow-up alert on the same entity during the window, the
incident reopens and escalates as `regressed`. If the window expires clean, the incident resolves as
`verified`. This provides *durable* confirmation (continued monitoring for recurrence) on top of M1's
*immediate* confirmation (the probe).

**Why this priority**: This is the gold standard for *durable* confirmation, but it is **gated on the detector
(#14)**, which is built later in the sequence — so it is documented here as a marked milestone and built only
once #14 lands. M1 already delivers a complete, honest verdict without it. Per Constitution I, no buildable
requirement in this spec depends on #14; M2's requirements are explicitly deferred-until-#14.

**Independent Test** *(deferred until #14 exists)*: With the detector available, apply a remediation, confirm
the incident parks in `verifying`; inject a recurrence alert on the same entity within the dwell window and
assert it reopens as `regressed`; in a separate run let the window expire clean and assert it resolves as
`verified`.

**Acceptance Scenarios** *(M2 — gated on #14)*:

1. **Given** an applied remediation and the detector available, **When** the response stage completes, **Then**
   the incident parks in the `verifying` state for the configured dwell window using the existing park/resume
   machinery (no new mechanism).
2. **Given** an incident in `verifying`, **When** a follow-up alert fires on the same entity within the window,
   **Then** the incident reopens and escalates with verdict `regressed`.
3. **Given** an incident in `verifying`, **When** the dwell window expires with no recurrence, **Then** the
   incident resolves with verdict `verified`.

---

### Edge Cases

- **Conflicting signals** — indicator re-check says still-malicious but the executor probe reports the expected
  clean post-state (or vice versa): the verdict is resolved determinism-first toward the **worse** outcome
  (`regressed` over `verified`); a reasoning (LLM) call is permitted **only** when the signals genuinely
  conflict and a deterministic rule cannot resolve them, never otherwise.
- **Multiple applied actions on one incident** — the incident-level verdict is the worst-case across its
  applied actions (`regressed` > `unverified` > `verified`), so one unconfirmed action prevents a blanket
  success claim.
- **Indicator-less remediation** — a remediation with no re-checkable indicator (e.g. `open_ticket` only)
  relies on the executor probe alone; if the probe is inconclusive the verdict is `unverified`.
- **Verification step itself errors** — treated as `unverified` (fail-closed); the error is recorded but never
  blocks the incident from reaching a terminal/escalated state.
- **Re-run after a verdict already recorded** — idempotent: no disposition change, no duplicate audit/evidence
  rows.
- **Approved-then-verified-as-regressed** — a human approved the action, but verification finds the threat
  persists: the incident does **not** silently resolve; it escalates as `remediation_unverified` so a human
  re-engages.
- **(M2) Late recurrence after window expiry** — a recurrence arriving after a `verified` window has closed is
  a new incident through normal ingestion, not a reopen of the closed one.

## Requirements *(mandatory)*

### Functional Requirements

**M1 — probe verdict (buildable now)**

- **FR-001**: After any remediation in which at least one action reached an *applied* state — on **both** the
  auto path and the human-approved path — the system MUST compute exactly one verification verdict for the
  incident before it reaches a terminal disposition.
- **FR-002**: The verdict MUST be produced by a **deterministic** step at the tail of the response stage (no
  new agent, no new pipeline stage in M1). A reasoning (LLM) call is permitted **only** when the deterministic
  signals genuinely conflict and a rule cannot resolve them; in all other cases no LLM call is made
  (Constitution IV — determinism first).
- **FR-003**: The verdict MUST combine (a) an **indicator re-check** that re-queries the targeted indicator's
  **current time-valid** reputation/fact state through the existing retrieval paths (reference-corpus / intel
  reputation and the temporal-memory fact query), and (b) an **executor status probe** that reads the observed
  post-state from the executor.
- **FR-004**: The executor status probe MUST be **contract-shaped to accept a real EDR/firewall/control-plane
  probe later** without changing the verdict logic — mock environment now, real connector a drop-in.
- **FR-005**: The system MUST classify the verdict as exactly one of: `verified` (expected post-state observed
  — indicator now blocked/clean and the entity's state reflects the action), `unverified` (no confirmation
  either way — probe inconclusive or indicator state unknown/unchanged), `regressed` (threat persists or
  reappears — indicator still malicious, or recurrence observed in M2).
- **FR-006**: On `verified`, the incident MUST keep its `remediated` / `auto_remediated` disposition. On
  `unverified` or `regressed`, the system MUST set the **`remediation_unverified`** disposition and **escalate**
  rather than resolving (Constitution V — escalate rather than false-resolve).
- **FR-007**: The system MUST NOT report or imply threat **elimination** for any incident whose verdict is
  `unverified`. A verdict the system cannot confirm is never surfaced as success.
- **FR-008**: Verification MUST be **fail-closed and best-effort**: when a required signal is unavailable
  (intel/reputation unknown, memory retrieval errors, probe inconclusive) the verdict MUST default to
  `unverified` (never `verified`), and a verification failure MUST never prevent the incident from reaching a
  terminal/escalated state.
- **FR-009**: The verification step MUST be **idempotent** and safe to re-run on worker resume — recomputing a
  verdict for an incident that already carries one MUST NOT change its disposition or duplicate evidence/audit
  records.
- **FR-010**: The verdict and the (redacted) evidence it considered — which signals were checked, their
  results, and whether a tiebreak reasoning call was used — MUST be recorded on the incident's response-stage
  evidence record (the verification slot reserved in #10) so the dashboard trace can render it. All sensitive
  values MUST pass redaction before egress (Constitution III).
- **FR-011**: Verification MUST run identically on the auto-remediation path and the human-approved
  remediation path; an approved action that verification finds `regressed`/`unverified` MUST escalate, not
  silently resolve.

**M2 — monitoring loop (gated on #14; deferred until the detector exists)**

- **FR-012**: After an applied remediation, the incident MUST be able to park in a new **`verifying`** state for
  a configured dwell window, **reusing the existing park/resume machinery** (the mechanism used for
  `awaiting_approval`) — no new parking mechanism.
- **FR-013**: A follow-up alert on the same entity fired during the dwell window MUST reopen and escalate the
  incident with verdict `regressed`; a dwell window that expires with no recurrence MUST resolve the incident
  with verdict `verified`.
- **FR-014**: The dwell window length MUST be a **config-backed** value (Constitution VII), fixed at planning
  time, never hardcoded in stage logic.

### Key Entities

- **Verification verdict** — one of `verified` / `unverified` / `regressed`. The enum was **reserved** in
  `010-response-remediation` and is **activated** here (no new vocabulary invented).
- **Verification record** — the verdict plus the redacted evidence considered (indicator-recheck result,
  executor-probe result, whether a tiebreak reasoning call ran). Attached to the incident's response-stage
  evidence via the **`verification` slot reserved on the action result** in #10. Persisting this verdict back
  to temporal memory as a queryable time-valid fact is owned by **#16 (feedback loop)** — #15 produces and
  records it on the incident; #16 writes it back.
- **`remediation_unverified` disposition** — the terminal disposition for `unverified` / `regressed`
  incidents. **Reserved** in #10, **activated** here; it escalates rather than resolving as success.
- **Indicator re-check signal** — the targeted indicator's current time-valid reputation/fact state, obtained
  via the existing reference-corpus / intel and temporal-memory query paths (a **real** data path).
- **Executor status probe signal** — the observed post-state reported by the executor (mock now;
  contract-shaped for a real connector). Honestly labeled as **synthetic but contract-real**.
- **`verifying` state (M2)** — a new supervisor FSM state for the dwell-window park; introduced only with M2,
  reusing existing park/resume edges.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a labeled set of post-remediation states, the system classifies `verified` / `unverified` /
  `regressed` correctly at or above the committed **verification-accuracy** threshold, and the gate passes
  under **each configured reasoning provider** (Constitution II) before the tier is frozen.
- **SC-002**: 100% of incidents reaching a `remediated` / `auto_remediated` disposition carry a recorded
  verification verdict; no incident is resolved as remediated without one.
- **SC-003**: 0 false-success claims — no incident whose verdict is `unverified` or `regressed` is ever
  reported as remediated; every such incident escalates as `remediation_unverified`.
- **SC-004**: When verification signals are unavailable, 100% of verdicts fail closed to `unverified` (never
  `verified`).
- **SC-005**: Verification never blocks disposition — in 100% of runs where retrieval or the probe fails, the
  incident still reaches a terminal/escalated state, and the verification step stays within the existing
  per-incident step/token budget (no measurable regression to median time-to-disposition).
- **SC-006**: Re-running verification on an already-verified incident produces zero disposition changes and
  zero duplicate records (idempotence) across repeated worker resumes.
- **SC-007**: An analyst can read the verification verdict for any remediated incident in the dashboard trace,
  `remediation_unverified` incidents are distinguishable in the queue, and a verdict-breakdown KPI is present —
  with no secret appearing unredacted in any of these views.
- **SC-008** *(M2, gated on #14)*: In 100% of labeled recurrence cases, a `regressed` remediation is reopened
  within the configured dwell window via the monitoring loop.

## Assumptions

- **Build scope today is M1.** M2 (the `verifying` dwell-window monitoring loop) is **gated on the detector
  (#14)** and is documented here as a marked, deferred milestone; no buildable M1 requirement depends on #14,
  honoring Constitution I (no spec is invalid pending a later spec).
- **Reserved contracts are activated, not invented.** The `VerificationVerdict` states, the `verification`
  field on the action result, and the `remediation_unverified` disposition were reserved in
  `010-response-remediation`; this feature activates them, keeping contract churn minimal.
- **Mock-environment honesty (per roadmap §6.4).** The executor status probe and the M2 recurrence are
  **simulated against the mock environment**; the indicator re-check is a **real** re-query path. The
  real-EDR/firewall integration is contract-shaped but **not wired**. No spec text implies real-world efficacy.
- **Verification reads redacted state** — it never de-redacts; de-redaction is out of scope.
- **Verification runs only on applied remediations** — actions that ended `failed` / `not_executed` already
  escalate via the existing #10 path and are not re-verified.
- **Incident-level verdict is worst-case** across an incident's applied actions (`regressed` > `unverified` >
  `verified`).
- **Memory write-back is #16's job.** Producing and recording the verdict on the incident is #15; writing it
  to temporal memory as a queryable time-valid fact (so future incidents bias on it) is #16 (feedback loop).
- **Dwell-window length (M2) and any verdict-rule thresholds** are config-backed values to fix during
  `/speckit-plan`, not here (roadmap §6.3).
- **Dependencies**: #10 (response/remediation §v2c contracts), #5 (reference corpus / intel reputation) and #6
  (temporal-memory fact query) for the indicator re-check; M2 additionally on #14 (detector).

## Out of Scope

- **De-redaction** of any verification-read state.
- **Real EDR / firewall / control-plane integration** — verification runs against the mock environment; the
  real path is contract-shaped only.
- **Claiming elimination on `unverified`** — explicitly forbidden.
- **Model retraining of any kind** — the "gets smarter" capability is memory/retrieval, owned by #16.
- **Consuming the verdict to tune future triage/severity/routing/playbook selection** — that is #16
  (feedback loop); #15 only produces and records the verdict.
- **Writing the verdict back to temporal memory as a queryable fact** — owned by #16.
- **The detector itself (#14)** and any live network capture.
