# Contract — Supervisor-Routing Eval Gate

**Component**: #7 activates the **supervisor-routing** gate in `config/eval_thresholds.yaml` (seeded as a
placeholder on day 1, per Constitution II). It answers the brief's question: *"did each incident reach the
correct next stage?"*

## Why this gate is deterministic (and provider-independent)

Routing is a **pure function** of `(incident, SupervisorSettings)` and `(state, StageOutcome)` — see
[data-model.md §4/§6](../data-model.md). The supervisor makes **no LLM call** (SC-006), so the gate needs
**no LLM and no both-providers run**: it is fast, flake-free, and a true regression guard for the
determinism contract (Constitution IV).

## Fixtures → expected outcome

A small labeled set under `tests/fixtures/incidents/` (each a grounded `Incident` + the expected routing).
Stage handlers are driven by **scripted fakes** so the expected path is unambiguous.

| Fixture | Grounded input | Expected |
|---------|----------------|----------|
| `noise_low` | `severity = low` | fast-path → `resolved` (`auto_resolved_noise`), **0 stage calls** |
| `critical_high` | `severity = critical` | fast-path → `responding` (skip triage/enrichment) |
| `ambiguous_resolved_at_triage` | `severity = medium`, triage fake → `RESOLVED` | `triaging → resolved` (`auto_resolved_triage`), **0 enrichment calls** |
| `ambiguous_full_depth` | `severity = high`, triage → `ADVANCE`, enrichment → `ADVANCE`, response → `RESOLVED` | `triaging → enriching → responding → resolved` |
| `destructive_parks` | response fake → `NEEDS_APPROVAL` | `… → responding → awaiting_approval` (parked) |
| `indeterminate_severity` | `evidence.flags` has `severity_defaulted` | **not** fast-pathed → `triaging` |
| `stage_error_escalates` | triage fake raises non-retryable `ToolError` | `escalated` (`escalated_stage_error`), worker alive |
| `cap_breach_escalates` | fake stages loop `ADVANCE` past `max_steps` | `escalated` (`escalated_step_cap`) |

## Threshold

`supervisor_routing: 1.0` — **100%** of fixtures must route to their expected next stage / terminal
disposition (the routing is deterministic, so anything below 1.0 is a regression). The gate runs in the
unit tier (no DB/LLM) for speed and is wired into CI alongside the existing `smoke` and `redaction` gates.

## Out of scope for this gate

- **Triage F1**, **retrieval hit@k/MRR**, **temporal-memory**, **red-team**, **redaction** — owned by their
  components (#8, #9/#6, #11, #2). This gate asserts **routing correctness only**.
