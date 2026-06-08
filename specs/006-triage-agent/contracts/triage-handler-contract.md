# Contract — Triage Stage Handler (fills the #7 `run_triage` seam)

**Component**: #8 fills the triage handler the #7 stage-handler contract reserved. The frozen seam
(`StageHandler = Callable[[Incident], Awaitable[StageResult]]`) is **unchanged**; #8 injects its
dependencies by closure, not by widening the signature.

## Construction (DI by closure factory)

```python
# backend/agents/triage.py
def make_triage_handler(llm: LlmClient, cfg: TriageSettings) -> StageHandler:
    async def run_triage(incident: Incident) -> StageResult:
        ...
    return run_triage
```

- `llm`: the lifespan-singleton `LlmClient` (#3) — the **only** outbound capability triage holds.
- `cfg`: typed `TriageSettings` (#4 config) — thresholds, output budget, temperature, prompt version.
- **No** DB session, **no** action client, **no** transition authority is passed (structural Constitution
  III boundary). The returned closure matches the frozen `StageHandler` shape exactly.

### Wiring

- `SupervisorProvider.build` reads `container.llm` + `settings.triage` and registers
  `StageName.TRIAGE: make_triage_handler(llm, cfg)`. Enrichment/response remain bare stubs for now.
- `worker.py` registers `register_llm_provider()` **before** `SupervisorProvider` so `container.llm` is
  built first (build order = registration order; siblings read via `settings._container`).
- Tests build the handler directly with a **fake `LlmClient`** — no provider, no network.

## Input — the bounded read slice

The handler reads from the grounded `Incident`, using **only** the evidence assembled for it:

| Source | Used for |
|--------|----------|
| `incident.evidence.verdict` | the detector's verdict (triage does not re-detect) |
| `incident.evidence.severity` | ingested severity band (context only; not overwritten) |
| `incident.evidence.normalized_event` | structured event fields |
| `incident.evidence.summary` | grounding summary |
| `incident.evidence.retrieved_context` | typically **empty** in v1 (memory is #6/#9) — empty is normal |
| `incident.correlation_id` | passed to `llm.generate(correlation_id=…)` for tracing |

Evidence is **already redacted** (#2/#4). Triage reasons over this slice only — never trained priors, never
the raw alert (FR-005).

## Output — `StageResult`

Exactly one of three outcomes (FR-003), produced via the pure `decide_outcome` (see `data-model.md` §4):

| Outcome | When | Disposition | Supervisor result |
|---------|------|-------------|-------------------|
| `ADVANCE` | `verdict=real`, `confidence ≥ advance_min` | — | → `enriching` |
| `RESOLVED` | `verdict=noise`, `confidence ≥ resolve_min` | `auto_resolved_triage` | → `resolved` (adaptive: **zero** further stages — FR-014) |
| `ESCALATE` | `uncertain`, or `confidence < advance_min`, or noise below `resolve_min` | `escalated_triage` | → `escalated` |

Plus on every successful judgment: `tokens_consumed` (from `response.usage`), `evidence_patch={"triage": …}`
(the supervisor merges + persists — FR-010), and a short redacted `note`.

Triage **never** returns `NEEDS_APPROVAL` (response-only). An out-of-set outcome would be rejected by the
supervisor as an illegal transition — a structural guard against a hijacked stage.

## Failure — `ToolError` (fail-closed; see `research.md` TD7)

| Cause | Raised | Supervisor effect |
|-------|--------|-------------------|
| `LlmError(TRANSIENT)` / `EXHAUSTED` | `ToolError(retryable=True, kind="llm_transient"/"llm_exhausted")` | retry ≤ `max_stage_retries`, then `escalated` |
| `LlmError(AUTH/INVALID_REQUEST/CONTENT_REFUSAL)` | `ToolError(retryable=False, kind=…)` | `escalated` immediately |
| `response_schema` unmet / Pydantic / OOV verdict (malformed output) | `ToolError(retryable=False, kind="malformed_output")` | `escalated` (fail-closed) |

**Invariants enforced here:**
- Triage makes **exactly one** `llm.generate` call per incident (FR-009, SC-006).
- Triage **never** returns `RESOLVED`/`ADVANCE` on unvalidated, partial, or errored output (FR-007, SC-005).
- Triage **never** writes incident state and holds **no** action tools — worst case under injection is a
  wrong verdict, never an action (FR-006, SC-004).
- Triage **never** crashes the worker — every failure is a typed `ToolError` (or is caught by the
  supervisor's existing catch-all) (FR-008).

## What this contract deliberately does NOT cover

- The exact prompt text and `response_schema` JSON → `triage-judgment-schema.md`.
- The eval gate (labeled set, F1 threshold, both-providers) → `triage-eval.md`.
- Memory/intel retrieval that would populate `retrieved_context` (#6/#9) — out of scope here.
