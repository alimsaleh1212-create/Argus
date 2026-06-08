# Contract — Stage Handler Interface (the seam to #8 / #9 / #10)

**Component**: #7 defines this contract; **#8 (triage)**, **#9 (enrichment)**, **#10 (response)** fill the
handler bodies. #7 ships **stub handlers** so the spine is testable and demoable before the agents land.

## The interface

```python
# domain/pipeline.py  (pure types, no outward imports)
StageHandler = Callable[[Incident], Awaitable[StageResult]]
```

A handler is an **async function** that takes the `Incident` (its bounded read slice) and returns a
`StageResult`, or raises `ToolError`. The supervisor holds a registry
`{StageName.TRIAGE: run_triage, StageName.ENRICHMENT: run_enrichment, StageName.RESPONSE: run_response}`,
injected at construction and **substitutable in tests**.

### Inputs (the bounded slice)

The handler reads from the grounded `Incident` — primarily `evidence`, `normalized_event`, `severity`, and
(later) retrieved context. It is handed **no DB session, no action client, no transition authority**
(except the response handler, which #10 injects an action client into — and even then it *returns*
`NEEDS_APPROVAL` rather than executing destructive actions itself).

### Output — `StageResult`

| Field | Meaning |
|-------|---------|
| `stage` | which stage produced this (must match the one invoked) |
| `outcome` | `RESOLVED` \| `ADVANCE` \| `NEEDS_APPROVAL` \| `ESCALATE` (see below) |
| `tokens_consumed` | tokens the stage's LLM call used (0 for #7 stubs / no-LLM paths); summed into the cap |
| `disposition` | optional fine-grained reason to record on a terminal/parked transition |
| `evidence_patch` | optional bounded slice the stage contributes; **the supervisor merges + persists it** (the stage never writes the DB) |
| `note` | short, **already-redacted** human-readable note for the trace |

### Outcome semantics per stage

| Stage | Valid outcomes | Meaning |
|-------|----------------|---------|
| `triage` | `RESOLVED` (close as noise/handled), `ADVANCE` (needs enrichment), `ESCALATE` (abstain → human) | real-vs-noise + severity judgement (#8) |
| `enrichment` | `ADVANCE` (→ response), `RESOLVED` (context closes it), `ESCALATE` | cross-correlation of external + internal context (#9) |
| `response` | `RESOLVED` (auto-remediated low-risk), `NEEDS_APPROVAL` (destructive → park), `ESCALATE` | playbook select + auto/approval policy (#10) |

Returning an outcome invalid for the current state → the supervisor routes to `escalated`
(`escalated_illegal_transition`). This is the structural guard: a prompt-injected stage cannot escape its
allowed edges.

### Failure — `ToolError`

```python
raise ToolError(retryable=True,  kind="intel_timeout", detail="…")   # transient → supervisor retries (≤ max_stage_retries)
raise ToolError(retryable=False, kind="bad_playbook",  detail="…")   # permanent → supervisor escalates immediately
```

## #7 stub behaviour (replaced by #8–#10)

| Handler | #7 stub returns |
|---------|-----------------|
| `run_triage` | `StageResult(stage=TRIAGE, outcome=ADVANCE, tokens_consumed=0)` — sends ambiguous incidents on to enrichment (so the full spine exercises in e2e) |
| `run_enrichment` | `StageResult(stage=ENRICHMENT, outcome=ADVANCE, tokens_consumed=0)` |
| `run_response` | `StageResult(stage=RESPONSE, outcome=RESOLVED, disposition="auto_remediated", tokens_consumed=0)` — or `NEEDS_APPROVAL` for a fixture flagged destructive, to exercise the park |

Stubs make **no LLM call** and read only the incident. Tests substitute fakes via the registry to drive
every transition (RESOLVED/ADVANCE/NEEDS_APPROVAL/ESCALATE, retryable/non-retryable `ToolError`).

## What this contract deliberately does NOT specify

- Each stage's **tools, prompts, and internal reasoning** (owned by #8/#9/#10).
- The **response action client** and the **audit row** written on execution (#10).
- **Memory/corpus retrieval** that fills `evidence.retrieved_context` (#5/#6) — `evidence_patch` is the
  channel, but its contents are those specs' concern.
