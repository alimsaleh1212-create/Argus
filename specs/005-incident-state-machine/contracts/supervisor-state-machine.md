# Contract — Supervisor State Machine

**Component**: #7 `SPEC-incident-state-machine` · the behavioural contract the worker and dashboard rely on.

The supervisor is the **single writer** of `incident.status` / `incident.disposition` and the **sole owner**
of transitions. It is **deterministic** (no LLM) and **runs in the worker** (no request path).

## Entry / idempotency

`Supervisor.run_incident(incident_id, repo)` reads the current persisted status and acts by **state class**:

| Current status | Action |
|----------------|--------|
| `grounded` | start: route the grounded incident (fast-path or ambiguous) |
| `triaging` / `enriching` / `responding` | resume from that stage |
| `awaiting_approval` | **no-op** (waits for `resume_incident`) |
| `resolved` / `escalated` / `failed` | **no-op** (idempotent) |
| `received` / `grounding` | reject — not a supervisor entry state |

Re-delivery (at-least-once) is therefore safe: a terminal/parked incident is a no-op; an in-flight one
resumes. Concurrency is guarded by atomic `advance_status` (transition applies iff status still matches the
expected `from`), so two workers can never double-advance.

## Allowed transitions

See [data-model.md §4](../data-model.md) for the full table. Invariants the contract guarantees:

- Every advance is one of the **enumerated edges**; any (state, outcome) not in the table → `escalated`
  with `disposition = escalated_illegal_transition`. **A stage cannot drive an illegal transition.**
- **Fast-path** (no stage/LLM call): `grounded → resolved` (obvious noise) and `grounded → responding`
  (obvious critical). Ambiguous: `grounded → triaging`.
- **Adaptive depth**: `triaging → enriching → responding`; triage/enrichment may short-circuit to
  `resolved`/`escalated`. Enrichment runs **only** if triage returned `ADVANCE`.
- **Park**: `responding → awaiting_approval` on `NEEDS_APPROVAL`; the loop stops (mechanism/timeout/audit
  are #10).
- **Terminal** = `resolved | escalated | failed`; no auto-outgoing edges.

## Bounded execution

- Before each stage call the supervisor checks `steps < max_steps` and
  `tokens_used + next ≤ max_tokens`; a breach transitions to `escalated`
  (`escalated_step_cap` / `escalated_token_cap`) and stops — **never an unbounded loop**.
- A stage raising a **retryable** `ToolError` is retried up to `max_stage_retries` (transient only); a
  **non-retryable** error, an exhausted retry budget, or any unexpected exception → `escalated`
  (`escalated_stage_error`). **The worker process never crashes or 500s.**

## Observability

- Each incident run opens a parent span; each stage call is a child span (a stage = a node in the trace
  tree). Span attributes carry `tokens_consumed`, the outcome, and the transition — all **redacted** via the
  #2 seam; no raw incident content is ever logged or spanned.
- `correlation_id` is bound for the whole run (`bind_incident`), so logs/spans/incident stitch together for
  the dashboard.

## Reserved seam — `resume_incident(incident_id, decision, repo)`

Applies the `awaiting_approval` resume edges (approve → `responding`; reject → `resolved`,
`disposition = rejected_by_human`). #7 implements the **transitions**; **#10 owns** the interrupt vehicle,
the approval **timeout** + its terminal state, the **audit rows**, and the actual **action execution**.

## Guarantees (mapped to spec success criteria)

| Guarantee | Spec |
|-----------|------|
| Every grounded incident reaches exactly one terminal disposition; never stuck in-flight | SC-001 |
| Step/token caps always hold; breach → terminal `escalated`, no loop, no crash | SC-002 |
| Obvious-class incidents resolve via the fast-path with **zero** stage calls | SC-003 |
| Injected stage failures → terminal/escalated; worker stays alive | SC-004 |
| Re-delivery is idempotent; in-flight resumes from persisted state | SC-005 |
| Supervisor makes zero LLM calls (orchestration layer imports no LLM client) | SC-006 |
| No unredacted secret/PII in any persisted field, log, or span | SC-007 |
