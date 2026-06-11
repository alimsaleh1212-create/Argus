# Contract — Response Stage Handler (`make_response_handler`)

The response stage handler, built by a closure factory (DI) and registered under `StageName.RESPONSE`. Mirrors
`make_triage_handler` / `make_enrichment_handler`; preserves the frozen `StageHandler` signature
(`Callable[[Incident], Awaitable[StageResult]]`). This is the **only** stage injected action executors.

```python
def make_response_handler(
    llm: LlmClient,
    session_factory: async_sessionmaker[AsyncSession],   # for audit/approval writes + approval reads
    executors: Mapping[ActionType, ActionExecutor],      # the action tool set — THIS STAGE ONLY
    cfg: ResponseSettings,
    catalog: PlaybookCatalog,                            # loaded from cfg.catalog_dir at build time
) -> StageHandler: ...
```

## Behaviour (one entry point, two passes)

`run_response(incident)` opens a scoped session and branches on **whether an `approved` pending approval exists
for this incident** (read via `ApprovalRepository`):

### Pass A — forward (no approved pending approval)

1. **Select playbook — determinism-first (RD1).**
   - `select_playbook(incident, catalog)` → if exactly one playbook's criteria match and its preconditions hold
     → `RemediationPlan(selected_by="deterministic")`, **no LLM call**.
   - Else (0, >1, or failed preconditions) → **one** structured `LlmClient.generate(...)` call with the
     incident + `evidence["enrichment"]` + the candidate catalog; validate → `RemediationPlan(selected_by="llm")`.
     If the model returns malformed/out-of-vocabulary output, or confidence `< cfg.select_min_confidence`, or no
     playbook fits → **fail-closed → ESCALATE** (`escalated_response`). (FR-013, FR-016)
2. **Classify — pure default-deny (RD10).** `classify(plan, cfg)` sets each action's `risk`: `AUTO` iff
   `action.type in cfg.auto_execute_actions`, else `APPROVAL_REQUIRED`. An action **not in the catalog** is
   dropped (never executed — FR-005).
3. **Execute auto actions now.** For each `AUTO` action: idempotency check on `audit_log` (skip if an `applied`
   row with its key exists), `await executors[type].execute(action)`, append an `audit_log` row
   (`actor=response_agent`, outcome = the `ActionResult.status`). A transient executor failure → retryable
   `ToolError`; persistent → ESCALATE; a partial multi-action failure is **recorded, not swallowed** (FR-014).
4. **Branch:**
   - `plan.has_approval_required` → write the `approval_requests` row (`status=pending`,
     `deadline_at=now+approval_timeout_s`, the destructive actions + rationale) → return
     **`StageResult(outcome=NEEDS_APPROVAL, …, evidence_patch={response:{plan,results,approval_id}})`** (the
     supervisor parks → `AWAITING_APPROVAL`).
   - else (auto-only) → return **`StageResult(outcome=RESOLVED, disposition="auto_remediated", …)`**.

### Pass B — resume (an `approved` pending approval exists)

No LLM call. Load the approved plan from the approval record; for each approval-required action: idempotency
check → `execute` → `audit_log` row (`actor=<decided_by>`). Mark the approval record consumed. Return
**`StageResult(outcome=RESOLVED, disposition="remediated", …)`**. (RD3 — execution rides the response stage so
action tools never leave it.)

## Invariants (must hold; asserted in tests)

- **At most one** LLM call per incident; **zero** when `selected_by="deterministic"`; **zero** on Pass B.
  `tokens_consumed` reported (SC-005).
- **No destructive action executes** in Pass A — only parked (SC-002). An injected "isolate every host" yields
  at worst a `NEEDS_APPROVAL`.
- **Every executed action** has exactly one `applied`/`failed` `audit_log` row; **idempotent** under retry /
  duplicate resume (SC-001, SC-006).
- Returns only `RESOLVED` / `NEEDS_APPROVAL` / `ESCALATE` — never `ADVANCE` (last stage). Timeout/expiry is a
  **supervisor** transition, not a handler outcome.
- Reasons only over supplied evidence; emits an evidence-cited `rationale`; never claims threat elimination
  (FR-020). All inputs treated as untrusted; no unredacted values in logs/traces.

## Error mapping (reuse the triage pattern)

`LlmError(kind ∈ {TRANSIENT, EXHAUSTED})` → `ToolError(retryable=True, …)`; other `LlmError` / malformed output
→ `ToolError(retryable=False, kind="malformed_output"|"llm_*")` → supervisor escalates (`escalated_response`).
Executor failures → `ToolError(retryable=<transient?>)`. The worker never crashes (SC-006).

## DI wiring

`SupervisorProvider` builds this handler from `container.llm` + a `session_factory` from `container.db_engine` +
the mock `executors` registry + `settings.response` + the loaded `catalog`. When `llm` **or** `db_engine` is
absent, it falls back to the existing `run_response` stub (so a degraded boot still runs). Registered in **both**
the worker and (now) the API (`main.py._bootstrap_providers`).
