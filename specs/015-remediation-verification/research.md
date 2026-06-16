# Phase 0 — Research: Remediation Verification (#15)

No `NEEDS CLARIFICATION` remained from the spec — the prioritized roadmap (`v_2_3_plan.md` §3) and the
reserved `010` contracts pin the design. This file records the design decisions and the alternatives
rejected, in the repo's `Decision / Rationale / Alternatives` format.

---

## D1 — Where the verification step runs

**Decision.** A deterministic step at the **tail of the existing response stage handler**
(`agents/response.py`), invoked in both terminal branches: `_pass_a` (auto-only → would-be RESOLVED) and
`_pass_b` (approved resume → would-be RESOLVED). No new pipeline stage, no new agent.

**Rationale.** The roadmap says "a deterministic step at the tail of `RESPONDING` … no new agent"
(Constitution IV). The response handler is already the only stage injected with executors (needed for the
probe), and it already owns the `applied` `ActionResult`s. Keeping verification in-stage preserves the
single-writer supervisor and the frozen `StageHandler` contract.

**Alternatives rejected.** (a) A new supervisor post-stage hook — complicates the pure FSM. (b) A fourth
agent — violates "no 4th agent" (Constitution scope) and the determinism-first principle.

---

## D2 — FSM disposition for `unverified`/`regressed`

**Decision.** Add **`StageOutcome.UNVERIFIED`** (`domain/pipeline.py`) and one transition edge
`(RESPONDING, UNVERIFIED) → (ESCALATED, DISP_REMEDIATION_UNVERIFIED)` (`services/supervisor.py`). `verified`
keeps the current RESOLVED branch (disposition `auto_remediated`/`remediated` passes through unchanged, per
RD8). The `DISP_REMEDIATION_UNVERIFIED` constant already exists (reserved).

**Rationale.** The `(RESPONDING, ESCALATE)` edge hardcodes `DISP_ESCALATED_RESPONSE`, so a handler-proposed
disposition cannot pass through there. A dedicated outcome keeps "no confident playbook" (`escalated_response`,
via `ToolError`) distinct from "remediation not confirmed" (`remediation_unverified`) — both are escalations
but for auditable different reasons (Constitution IV). It mirrors the existing `NEEDS_APPROVAL` precedent
exactly (a dedicated outcome with its own edge).

**Alternatives rejected.** Reusing `ESCALATE` and flipping the table disposition to `None` for pass-through:
the response handler does not currently emit `ESCALATE` (escalations go via `ToolError` →
`DISP_ESCALATED_STAGE_ERROR`), so making `ESCALATE` pass-through introduces a latent "escalate with `None`
disposition" risk for any future emitter. The new outcome is the lower-risk, more explicit choice.

---

## D3 — Executor status probe contract

**Decision.** Add `async def probe(self, action: RemediationAction) -> ProbeResult` to the `ActionExecutor`
protocol (`domain/response.py`). `ProbeResult` carries an observed `ProbeState`
(`expected` / `unexpected` / `inconclusive`) + redacted `detail`. Mock executors implement `probe()` to
return `expected` by default; a `build_regressed_executors(...)` test helper returns `unexpected`/
`inconclusive`. The signature is shaped to wrap a real EDR/firewall/control-plane status call later with no
verdict-logic change.

**Rationale.** The roadmap is explicit that M1's probe is "synthetic but contract-real (shaped to accept a
real EDR/firewall probe later)" and that the spec must "state this honestly." A separate `probe()` method
(not overloading `execute()`) keeps the read-only observation distinct from the write action — and keeps the
boundary auditable. Real connectors remain a drop-in, exactly like the existing `execute()` mocks.

**Alternatives rejected.** (a) Inferring post-state from the `execute()` `ActionResult` alone — that only
says *dispatched*, never *effective* (the whole gap this spec closes). (b) A standalone prober service —
over-engineered for a mock environment.

---

## D4 — Indicator re-check (the real data path)

**Decision.** Re-check the **target of each applied action** (`action.target`, mapped to an `EntityRef`
kind) via the **same retrieval path enrichment already uses**: `ThreatIntelClient.lookup(target, kind)` →
`IntelVerdict`, and `MemoryStore.query_fact(entity, "reputation", as_of=None)` → `FactState` (current,
time-valid). Both are injected into `make_response_handler` (optional; `None` → that signal absent).

**Rationale.** This is the roadmap's "real data path (re-queries the indicator's current time-valid state)"
via `#5 CorpusRetriever/intel` + `#6 MemoryStore.query_fact`. Reusing the enrichment fan-out pattern
(`_safe(...)` best-effort, `asyncio.gather`) keeps it idiomatic and fail-closed. `query_fact(as_of=None)`
returns the **current** time-valid reputation, honouring invalidate-not-delete (Constitution VI). Intel is
Redis-cached, so the re-check is cheap.

**Alternatives rejected.** Re-extracting indicators from raw evidence (enrichment's `extract_entities`) —
the *action targets* are the precise things remediated, so re-checking them is both cheaper and more
faithful than a broad re-extraction.

---

## D5 — Verdict logic (deterministic; conflict-only LLM tiebreak)

**Decision.** A **pure** `decide_verdict(signals: VerificationSignals) -> VerificationVerdict` per applied
action, aggregated to an **incident-level worst-case** (`regressed` > `unverified` > `verified`). Per-action
rules (config-backed verdict sets):

- indicator still `malicious`/`suspicious` **or** probe `unexpected` → `regressed`
- indicator `benign`/clean **and** probe `expected` → `verified`
- any signal `unknown`/`inconclusive` (and none `regressed`) → `unverified`
- **genuine conflict** (probe `expected` but indicator `malicious`, or vice-versa) → deterministically the
  worse outcome (`regressed`); an **optional, config-gated** LLM tiebreak (`verify_llm_tiebreak`,
  default `False`) may override *only* in this conflict case.

**Rationale.** Constitution IV — determinism first; "an LLM call only if signals genuinely conflict."
Defaulting the tiebreak off keeps the common path LLM-free and the eval gate provider-independent, while
honouring the roadmap's allowance for a conflict-only call. Worst-case aggregation prevents one unconfirmed
action from yielding a blanket success (FR edge case).

**Alternatives rejected.** Always-LLM verdict (violates IV, cost/latency, non-deterministic gate);
best-case aggregation (would let a single confirmed action mask a regressed one — unsafe).

---

## D6 — Settings location

**Decision.** Extend **`ResponseSettings`** (no new config section): `verify_remediation: bool = True`,
`verify_regressed_verdicts: list[str] = ["malicious", "suspicious"]`, `verify_llm_tiebreak: bool = False`,
and (reserved for M2) `dwell_window_s: int = 900`.

**Rationale.** Verification is a sub-concern of the response stage; reusing the `"response"` section avoids
touching `_KNOWN_ARGUS_SECTIONS` / `Settings` (minimal churn, matching the roadmap's "v1 reserved nearly all
of it"). `extra="forbid"` and the env-var contract are inherited unchanged.

**Alternatives rejected.** A dedicated `VerificationSettings` section — cleaner separation but more churn
(new section registration, new `Settings` field, new env namespace) for a tightly-coupled response sub-step.

---

## D7 — M1 / M2 split and the M2 `verifying` design

**Decision.** **M1** (probe + re-check verdict) is built now and is self-contained — **no migration**, no
new state (the incident still goes RESPONDING → RESOLVED or RESPONDING → ESCALATED). **M2** (monitoring
loop) is **designed-but-deferred, gated on the detector #14**: it adds `IncidentStatus.VERIFYING` (text
status, no migration) and parks the remediated incident for `dwell_window_s`, **reusing the
`awaiting_approval` park/resume machinery** (`advance_status` guarded edges + a deadline sweeper analogous to
`expire_incident`). A `#14` recurrence alert on the same entity reopens as `regressed`; clean expiry →
`verified`.

**Rationale.** Constitution I — no buildable M1 requirement may depend on a later spec. The roadmap gates M2
on #14 and mandates "reusing the park/resume machinery #5 reserved and #10 uses … no new mechanism." Keeping
M2 design-only here means the seam is reserved without dark, un-testable code.

**Alternatives rejected.** Building M2 now with a stubbed recurrence source — would create un-exercisable
FSM paths (no detector to fire recurrences), violating "tests green every day."

---

## D8 — The verification eval gate

**Decision.** A new **`verification`** gate: a block in `config/eval_thresholds.yaml` **and** a registered
runner `backend/eval/gates/verification.py`, added in the **same** change. Deterministic (provider-
independent like `supervisor_routing`): drives a labeled fixture set of post-remediation states
(`tests/fixtures/verification/`) through `decide_verdict` and scores classification accuracy against a
committed threshold. The existing **temporal-memory** gate is extended to cover the verification fact's
time-validity, and the **redaction** gate's boundary set already covers the verification record + dashboard
view (no new boundary).

**Rationale.** Per #13, the harness enforces a **declared⇔registered orphan/stale check as a hard error**,
so the yaml block and the registry entry must land together. The roadmap assigns this gate to #15 and says
to *extend* (not duplicate) temporal/redaction.

**Alternatives rejected.** An LLM-judge verification gate — unnecessary because the verdict is deterministic;
a deterministic gate is cheaper, provider-independent, and 100%-pass-able like routing.

---

## D9 — Memory write-back boundary (#15 vs #16)

**Decision.** #15 **produces and records** the verdict on the incident (evidence-patch + optional audit
row). Writing the verdict back to **temporal memory as a queryable time-valid fact** (so future incidents
bias on it) is **#16 (feedback loop)**. #15's re-check only *reads* memory.

**Rationale.** Constitution III — verification introduces "no new write authority beyond #6's path," and
#15 does not even use that write path (it reads). Cleanly splitting produce (#15) from write-back (#16)
matches the roadmap seam ("015 produces the verdict it writes").

**Alternatives rejected.** #15 writing the fact itself — would duplicate #16's contract and add write
authority this spec is explicitly scoped not to add.

---

## D10 — Idempotency & persistence

**Decision.** No new table for M1. The verdict rides the existing `incidents.evidence` JSONB via the
response evidence-patch (`evidence["response"]["verification"]`), and each `ActionResult.verification` is
stamped. Re-run safety: terminal/parked incidents are already a supervisor no-op; within a run, if a
verification record is already present the step is skipped (no double-probe, no duplicate audit row).

**Rationale.** Mirrors #9/#10's zero-migration, evidence-patch persistence and the existing idempotency
posture (`audit_repo.is_applied` for actions; terminal no-op for resume). Constitution VII — Pydantic at the
boundary; single-writer supervisor persists the patch.

**Alternatives rejected.** A dedicated `verification_records` table — unjustified for M1 (the verdict is a
small, incident-scoped record already carried by `evidence`); revisit only if M2/#16 need indexed queries.
