# Phase 1 — Data Model: Incident State Machine (Supervisor)

**Component**: #7 `SPEC-incident-state-machine` · **Date**: 2026-06-08

This component **extends** the #4 Incident contract (it never re-declares it) and adds the small set of
pure types the state machine needs. New domain types live in `backend/domain/pipeline.py` (no outward
imports — domain-isolation `import-linter` contract); they are the **single contract** imported by the
agents (#8–#10) and the dashboard (#12).

---

## 1. Extended `IncidentStatus` (in `domain/incident.py`)

`status` is stored as `text`, so adding values needs **no migration** (anticipated by #4).

```python
class IncidentStatus(StrEnum):
    # --- from #4 (ingestion) ---
    RECEIVED   = "received"     # persisted at intake, enqueued
    GROUNDING  = "grounding"    # claimed by the worker
    GROUNDED   = "grounded"     # evidence ready → supervisor entry point
    # --- added by #7 (supervisor) ---
    TRIAGING   = "triaging"     # in-flight: triage stage running
    ENRICHING  = "enriching"    # in-flight: enrichment stage running
    RESPONDING = "responding"   # in-flight: response stage running
    AWAITING_APPROVAL = "awaiting_approval"  # parked (non-terminal) — destructive action needs a human (#10)
    RESOLVED   = "resolved"     # TERMINAL: auto-resolved / closed / auto-remediated
    ESCALATED  = "escalated"    # TERMINAL: handed to a human / degraded
    FAILED     = "failed"       # TERMINAL: from #4 (unrecoverable processing error)
```

**State classes** (used by the supervisor's entry decision, SD8):

| Class | Members | Supervisor behaviour on (re-)delivery |
|-------|---------|----------------------------------------|
| entry | `grounded` | start the pipeline |
| in-flight | `triaging`, `enriching`, `responding` | **resume** from this stage |
| parked | `awaiting_approval` | **no-op** (waits for `resume_incident`, #10) |
| terminal | `resolved`, `escalated`, `failed` | **no-op** (idempotent) |
| pre-entry | `received`, `grounding` | not handed to the supervisor (worker owns these) |

---

## 2. `Incident` addition (in `domain/incident.py`)

One optional field is added; the table gains one nullable column (migration `0004`).

| Field | Type | Column | Notes |
|-------|------|--------|-------|
| `disposition` | `str \| None` | `disposition text null` | fine-grained terminal reason (see §5). `None` until terminal/parked. |

All other `Incident` fields are unchanged from #4. Per-incident **step/token counts are NOT stored** — they
are enforced in-memory and emitted as trace-span attributes (#2). The dashboard reads coarse outcome from
`status`, the reason from `disposition`, and per-stage tokens/latency from `trace_spans`.

---

## 3. New pure types (in `domain/pipeline.py`)

```python
class StageName(StrEnum):
    TRIAGE     = "triage"
    ENRICHMENT = "enrichment"
    RESPONSE   = "response"

class StageOutcome(StrEnum):
    RESOLVED      = "resolved"        # close the incident here (terminal → resolved)
    ADVANCE       = "advance"         # go to the next stage (adaptive depth)
    NEEDS_APPROVAL = "needs_approval" # response only → park in awaiting_approval
    ESCALATE      = "escalate"        # hand to a human (terminal → escalated)

class StageResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: StageName
    outcome: StageOutcome
    tokens_consumed: int = 0                 # aggregated into the per-incident token cap (0 for stubs / no-LLM)
    disposition: str | None = None           # optional fine-grained reason the stage proposes
    evidence_patch: dict[str, Any] | None = None  # optional slice the stage contributes (supervisor persists it)
    note: str | None = None                  # short, already-redacted human-readable note for the trace

class ToolError(Exception):
    """Structured stage failure (Constitution VII / brief). The supervisor inspects `retryable`."""
    def __init__(self, *, retryable: bool, kind: str, detail: str = "") -> None:
        self.retryable = retryable
        self.kind = kind
        self.detail = detail
```

- A stage handler **returns** a `StageResult` on success or **raises** `ToolError` on failure. `retryable`
  governs whether the supervisor retries (transient) or degrades immediately (SD4/SD7).
- `evidence_patch` is how a stage contributes its **bounded slice** without writing the DB — the supervisor
  merges and persists it (single-writer, SD4). For #7 stubs this is `None`.

---

## 4. The transition table (owned by `services/supervisor.py`)

The single source of allowed edges. Any (state, outcome) pair **not** in the table is **illegal** → the
supervisor routes to `escalated` (`disposition = escalated_illegal_transition`) — a structural guard
against a hijacked/buggy stage (FR-002, edge case).

| From state | Trigger | To state | Disposition (on terminal) |
|------------|---------|----------|---------------------------|
| `grounded` | route: severity ∈ autoclose | `resolved` | `auto_resolved_noise` |
| `grounded` | route: severity ∈ critical | `responding` | — |
| `grounded` | route: ambiguous | `triaging` | — |
| `triaging` | `StageOutcome.RESOLVED` | `resolved` | `auto_resolved_triage` |
| `triaging` | `StageOutcome.ADVANCE` | `enriching` | — |
| `triaging` | `StageOutcome.ESCALATE` | `escalated` | `escalated_triage` |
| `enriching` | `StageOutcome.ADVANCE` | `responding` | — |
| `enriching` | `StageOutcome.RESOLVED` | `resolved` | `auto_resolved_enrichment` |
| `enriching` | `StageOutcome.ESCALATE` | `escalated` | `escalated_enrichment` |
| `responding` | `StageOutcome.RESOLVED` | `resolved` | `auto_remediated` |
| `responding` | `StageOutcome.NEEDS_APPROVAL` | `awaiting_approval` | `awaiting_approval_destructive` (parked) |
| `responding` | `StageOutcome.ESCALATE` | `escalated` | `escalated_response` |
| `awaiting_approval` | `resume(approve)` | `responding` *(re-run to execute)* | — *(mechanism: #10)* |
| `awaiting_approval` | `resume(reject)` | `resolved` | `rejected_by_human` *(mechanism: #10)* |
| *any in-flight* | step cap exceeded | `escalated` | `escalated_step_cap` |
| *any in-flight* | token cap exceeded | `escalated` | `escalated_token_cap` |
| *any in-flight* | non-retryable `ToolError` / retries exhausted | `escalated` | `escalated_stage_error` |
| *any (state, outcome) not above* | — | `escalated` | `escalated_illegal_transition` |

Terminal states (`resolved`, `escalated`, `failed`) and the parked `awaiting_approval` have **no
auto-outgoing edges** (the loop stops). `triaging → responding` is intentionally **not** a direct edge:
adaptive depth routes triage's "needs action" through enrichment (`ADVANCE`); a triage that wants to skip
to response returns `RESOLVED`/`ESCALATE` instead. (Obvious-critical skipping straight to response is the
**fast-path** edge from `grounded`, not from `triaging`.)

---

## 5. Disposition vocabulary (the `disposition` column)

Fine-grained, queryable reasons (coarse outcome stays in `status`):

| `status` | `disposition` values |
|----------|----------------------|
| `resolved` | `auto_resolved_noise`, `auto_resolved_triage`, `auto_resolved_enrichment`, `auto_remediated`, `rejected_by_human` |
| `escalated` | `escalated_triage`, `escalated_enrichment`, `escalated_response`, `escalated_step_cap`, `escalated_token_cap`, `escalated_stage_error`, `escalated_illegal_transition` |
| `awaiting_approval` | `awaiting_approval_destructive` |
| `failed` | (from #4 — set by the worker, not the supervisor) |

---

## 6. Routing decision (deterministic, config-backed — SD5)

```python
def route_grounded(incident: Incident, cfg: SupervisorSettings) -> IncidentStatus:
    sev = incident.severity
    flags = (incident.evidence or {}).get("flags", [])
    if "severity_defaulted" in flags:          # indeterminate ⇒ never fast-path (edge case)
        return IncidentStatus.TRIAGING
    if sev in cfg.fast_path_autoclose_severities:   # default {"low"}  → no stage call
        return IncidentStatus.RESOLVED
    if sev in cfg.fast_path_critical_severities:    # default {"critical"}
        return IncidentStatus.RESPONDING
    return IncidentStatus.TRIAGING                  # medium / high ⇒ ambiguous full depth
```

Pure function of the grounded incident + config → unit-testable, reproducible, eval-able (SD10).

---

## 7. `SupervisorSettings` (in `infra/config.py`)

New typed section (`extra="forbid"`), registered on `Settings`; `_KNOWN_SENTINEL_SECTIONS` gains
`"supervisor"`. Env vars: `SENTINEL__SUPERVISOR__MAX_STEPS`, etc.

```python
class SupervisorSettings(BaseSettings):          # SENTINEL__SUPERVISOR__*
    model_config = SettingsConfigDict(extra="forbid")
    max_steps: Annotated[int, Field(gt=0)] = 8                 # hard step cap → escalated
    max_tokens: Annotated[int, Field(gt=0)] = 40_000          # hard per-incident token cap → escalated
    max_stage_retries: Annotated[int, Field(ge=0)] = 2        # transient ToolError retries only
    fast_path_autoclose_severities: list[str] = ["low"]       # obvious noise → resolved, no stage call
    fast_path_critical_severities: list[str] = ["critical"]   # obvious critical → straight to response
```

No new Vault path (the supervisor holds no secret and makes no LLM/network call of its own).

---

## 8. Repository addition (in `repositories/incidents.py`)

One guarded transition method (the `claim_for_grounding` idiom, SD8); existing `get`, `set_grounded`,
`list_non_terminal`, `mark_failed` are reused.

```python
async def advance_status(
    self,
    incident_id: uuid.UUID,
    *,
    expected: IncidentStatus,
    target: IncidentStatus,
    disposition: str | None = None,
) -> bool:
    """Atomic guarded transition: UPDATE … SET status=:target[, disposition=:disp]
       WHERE id=:id AND status=:expected RETURNING id. Returns True iff applied."""
```

The supervisor calls `advance_status` for every edge; a `False` return (someone else moved the row) ends
this worker's run for that incident — idempotency falls out of the guard.

---

## 9. Migration `0004_incident_disposition`

```python
revision = "0004"; down_revision = "0003"

def upgrade() -> None:
    op.add_column("incidents", sa.Column("disposition", sa.Text, nullable=True))

def downgrade() -> None:
    op.drop_column("incidents", "disposition")
```

Follows the `0003_incidents` shape; reversible; no data backfill (existing rows keep `disposition = NULL`).
Status values are not enumerated in the DB (stored as `text`), so the new statuses need no DDL.

---

## 10. Wiring (lifespan singleton + seam delegation)

- **`SupervisorProvider`** (in `infra/`, mirrors `QueueProvider`/`CacheProvider`) builds one `Supervisor`
  at startup holding: the **stage-handler registry** (`{TRIAGE: run_triage, ENRICHMENT: run_enrichment,
  RESPONSE: run_response}`, substitutable in tests), `SupervisorSettings`, and the tracer. Exposed as
  `container.supervisor`.
- **`services/pipeline.py:dispatch_to_pipeline(incident, repo=None)`** — backward-compatible extension of
  the #4 seam (one-arg calls still valid): delegates to `container.supervisor.run_incident(incident.id,
  repo)`, loading the incident fresh from `repo` (the Postgres source of truth) so it sees the grounded
  state. The worker passes its session-bound `repo`.
- **`Supervisor.run_incident(incident_id, repo)`** — the loop: read status → (entry/resume/no-op) → route
  / call stage (with bounded retry on retryable `ToolError`) → check caps → `advance_status` → repeat until
  terminal/parked. Opens a `tracer.span()` per step (a stage = a child span) so an incident is a trace tree.
- **`Supervisor.resume_incident(incident_id, decision, repo)`** — reserved seam for #10: applies the
  `awaiting_approval` resume edges (§4). #7 implements the transitions; #10 fills the interrupt/timeout/
  audit/action-execution around them.
