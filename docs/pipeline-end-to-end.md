# Argus pipeline — end to end

How an alert moves from detection through intake, the supervisor's stages
(triage, enrichment, response), verification, and back through the memory
feedback loop. Grounded in the current code; file references point at the
collaborators that implement each step.

## Entry points (3 detection sources, all converge on one ingestion contract)

| Source | Path | Mechanism |
|---|---|---|
| Real Wazuh webhook | `POST /ingest/wazuh` | live alert, `source="wazuh"` |
| Rule/threshold detector (#14) | `python -m backend.detector` | deterministic match+threshold rules over replayed events → [services/detector.py](../backend/services/detector.py) `evaluate()`, `source="detector"` |
| ML anomaly detector (#17) | `python -m backend.anomaly_detector` | Isolation Forest scores per-entity-window behavioral deviation → [services/anomaly.py](../backend/services/anomaly.py), `source="anomaly-detector"` |

All three call the **same** [services/intake.accept()](../backend/services/intake.py): redact (fail-closed) → dedup fingerprint (Redis `SET NX EX`) → persist `Incident(RECEIVED)` in Postgres → enqueue → `202`/`IngestResult`. Duplicate fingerprint → returns the existing incident, no new row. This is why #14 and #17 needed zero changes downstream — `source` was the only new param, added by #14 and reused as-is by #17.

## Worker — grounding + feedback bias

[worker.py](../backend/worker.py) dequeues, claims the incident (idempotency guard), then:
1. `ground(incident)` — pure, deterministic `NormalizedEvent → Evidence` (severity, flags like `severity_defaulted`/`agent_unknown`, summary).
2. `_apply_feedback_bias()` — **read side of the feedback loop**: extracts entities, calls `gather_feedback()` against temporal memory for `remediation_outcome` facts on those same entities. If any current fact is `unverified`/`regressed`, it raises severity (`bump_one`/`to_critical`) and tags `flags += ["prior_failure"]`, before the incident is ever routed. Memory outage → no bias, never blocks (fail-open).
3. `set_grounded()` then `dispatch_to_pipeline()` → `Supervisor.run_incident()`.
4. **Post-terminal, off-path**: fire-and-forget task writes one `IncidentEpisode` to memory + (if terminal disposition came from verification) `record_outcome_facts()` — the **write side** of the feedback loop, keyed identically to the reputation fact so the read in step 2 of a *future* incident on the same entity hits.

## Supervisor — deterministic state machine

[services/supervisor.py](../backend/services/supervisor.py) is a plain async loop driven by a transition table — no LLM, single writer of `status`/`disposition`.

**GROUNDED routing** (`route_grounded`, config-backed, runs before any stage):
- `severity_defaulted` flag → **ambiguous** → `TRIAGING`
- severity in `fast_path_autoclose_severities` → **noise** → `RESOLVED` (`auto_resolved_noise`)
- severity in `fast_path_critical_severities` → **critical** → straight to `RESPONDING` (skips triage/enrichment entirely)
- else → **ambiguous** → `TRIAGING`

**TRIAGING** ([agents/triage.py](../backend/agents/triage.py), one LLM call, fail-closed on bad/malformed output → `ToolError`):
- verdict `uncertain`, or confidence below `advance_min_confidence` → `ESCALATE` (`escalated_triage`)
- verdict `real` + confident → `ADVANCE` → `ENRICHING`
- verdict `noise` + confidence ≥ `resolve_min_confidence` → `RESOLVED` (`auto_resolved_triage`)
- verdict `noise` but not confident enough → `ESCALATE`

**ENRICHING** ([agents/enrichment/handler.py](../backend/agents/enrichment/handler.py), read-only — no DB session, can't write state): bounded concurrent fan-out (corpus retrieval #8, `memory.search_similar`/`query_fact` #7, threat intel #8) + exactly one LLM call → `decide_outcome`:
- `ADVANCE` → `RESPONDING`
- `RESOLVED` (`auto_resolved_enrichment`)
- `ESCALATE` (`escalated_enrichment`)

**RESPONDING** ([agents/response/handler.py](../backend/agents/response/handler.py)) — the only stage with action executors:
- **Pass A** (forward): `select_playbook` (deterministic catalog match; feedback-aware — `prefer_stronger_playbook` swaps in a stronger playbook if a current failure-class signal exists; ambiguous tail = one LLM call) → `classify` into AUTO vs APPROVAL_REQUIRED actions → execute AUTO actions immediately (audited) → if any APPROVAL_REQUIRED actions exist, park: create `pending` approval row, transition to `AWAITING_APPROVAL` (`NEEDS_APPROVAL`/`awaiting_approval_destructive`) — **no verification yet**, since nothing destructive has been applied.
- **Approval resolution** happens outside `run_incident`, via `Supervisor.resume_incident()`: `approve` → back to `RESPONDING` and **Pass B** executes the already-approved actions (no LLM) → `remediated`; `reject` → `RESOLVED` (`rejected_by_human`) + audit row. A background sweeper (`_sweep_expired_approvals`) calls `expire_incident()` for stale pendings → `ESCALATED` (`approval_expired`), nothing executes.
- **Verification tail** (`_finalize_with_verification`, shared by both passes, idempotent — skipped if `evidence["response"]["verification"]` already exists): runs only if actions were actually `APPLIED`. For each applied action, concurrently `executor.probe()` (read-only post-state check) + indicator re-check (`intel.lookup` + `memory.query_fact`), aggregated **worst-case** (`REGRESSED > UNVERIFIED > VERIFIED`):
  - `VERIFIED` → `RESOLVED` (`auto_remediated` / `remediated`)
  - `UNVERIFIED` or `REGRESSED` → `StageOutcome.UNVERIFIED` → table edge `(RESPONDING, UNVERIFIED) → (ESCALATED, remediation_unverified)`

**Caps that override any in-flight stage**: `max_steps` and `max_tokens` → `ESCALATED` (`escalated_step_cap`/`escalated_token_cap`); any non-retryable or exhausted-retry `ToolError`, illegal transition, or unexpected exception → `ESCALATED` (`escalated_stage_error`/`escalated_illegal_transition`).

## Terminal states

- `RESOLVED` — noise, triage/enrichment auto-resolve, verified remediation, or human rejection
- `ESCALATED` — uncertain triage/enrichment, unverified/regressed remediation, approval timeout, caps, or stage errors
- `FAILED` — worker exhausted retry attempts before grounding even completed

## Feedback loop, closing the circle

Every terminal incident gets a write-back (`record_episode` + `record_outcome_facts`, keyed by the same entity refs used in step 2 above). The *next* incident touching that entity gets biased at grounding (severity bump + `prior_failure` flag) **and** at response-stage playbook selection (stronger playbook preference) — both deterministic, config-gated, fail-open if memory is down. This is what closes detection→response→verify→memory→(next) detection without giving the feedback path any write authority over incident status — that stays the supervisor's alone.
