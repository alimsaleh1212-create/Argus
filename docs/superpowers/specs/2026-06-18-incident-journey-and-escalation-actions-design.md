# Incident Journey Tags + Escalation Actions + Enrichment Fix — Design

**Date:** 2026-06-18
**Status:** Approved (design); pending spec review → implementation plan
**Branch:** `feat/incident-journey-and-escalation-actions`

## Problem

Three related gaps surfaced while preparing the demo:

1. **Escalated incidents are a dead end.** `ESCALATED` is terminal with no operator
   exit. Every escalation sits forever in the human-attention surface; there is no way
   to acknowledge ("I've seen it, drop it from my queue") or to close it out.
2. **Cards don't show how an incident travelled.** The detailed `EvidencePanel` exists
   in the drawer, but the cards/rows themselves give no at-a-glance trace of which
   stages an incident passed through, each stage's result, or its score.
3. **Enrichment + graph-RAG don't actually run in the demo.** `scripts/demo_full_workflow.sh`
   encodes the enrichment cases (`13_enrichment_benign`, `14_enrichment_escalate`) as
   *expected* `escalated:escalated_stage_error` with a `# LLM-dependent` comment — i.e.
   enrichment is entered but errors out, so no enrichment evidence and no graph-RAG
   retrieval ever land. Feature B has nothing real to display until this is fixed.

## Scope & sequencing

Built in dependency order:

1. **C — Fix enrichment / graph-RAG** (prerequisite: B needs C's data to be meaningful)
2. **B — Journey path tags on cards**
3. **A — Acknowledge + Resolve on escalated incidents** (independent)

Each thread is independently shippable and gets its own milestone in the plan.

---

## Thread C — Root-cause & fix enrichment + graph-RAG

This is a **diagnosis-then-fix**. The method is fixed; the exact patch is determined by
runtime evidence (systematic-debugging), not pre-decided here.

### Steps

1. **Reproduce.** Run the enrichment demo cases
   ([demo_full_workflow.sh:296-304](../../../scripts/demo_full_workflow.sh#L296-L304))
   against the live stack and capture the actual exception the supervisor converts to
   `escalated_stage_error`.
2. **Confirm which of two layers fails** before changing anything:
   - **Stage LLM call** — under the demo's likely Ollama-fallback path, the enrichment
     structured-output parse
     ([agents/enrichment/reasoning.py](../../../backend/agents/enrichment/reasoning.py)
     `report_from_response`) may raise `malformed_output`. Candidate fix: robust
     parsing / schema-coercion for the fallback provider.
   - **Graph-RAG retrieval returns nothing** — `MemorySettings.embedder_provider`
     defaults to `gemini`; with no Gemini key the Graphiti memory likely no-ops, and
     there is no seeding of episodes/facts for the demo entities, so `search_similar` /
     `query_fact` return empty. Candidate fix: ensure retrieval degrades visibly (logged,
     not silent) **and** seed a small demo memory set so retrieval returns real context.
3. **Fix** the confirmed cause(s).

### Exit criteria

- The enrichment demo cases reach a genuine enrichment verdict (`ADVANCE` / `RESOLVED`
  / `ESCALATE`) with non-empty `external_findings` / `internal_findings`.
- `demo_full_workflow.sh`'s expected-outcomes table is corrected to the true behavior
  (no longer asserting `escalated_stage_error` for enrichment cases).

### Constraints

- Enrichment retrieval stays **best-effort**: a retrieval miss must not error the stage
  (only the single LLM call is fatal). The fix must preserve this.
- No change to the enrichment stage contract (`StageHandler`, `evidence_patch` shape).

---

## Thread B — Journey path tags on cards

Show, per incident, the ordered path it took, each stage's outcome, and its score.

### Backend

- Add a pure, derived `journey: list[JourneyStep]` to the card DTO `IncidentSummary`
  and to `IncidentDetailView` (`backend/domain/dashboard.py`).
- `JourneyStep` fields: `stage` (key), `label` (display), `outcome` (advance / resolved
  / escalated / errored), `detail` (e.g. verdict, playbook id), `score`
  (float | None).
- Computed in [pipeline_view.py](../../../backend/services/pipeline_view.py) **purely**
  from `evidence` + `status` + `disposition`. **No new query, no migration.**
- Step derivation:
  - **Intake** — source-aware (`wazuh` / `detector` / `anomaly-detector`); surfaces the
    anomaly score when `source == "anomaly-detector"`.
  - **Triage** — verdict + confidence from `evidence.triage`.
  - **Enrichment** — assessment + confidence from `evidence.enrichment`.
  - **Response** — playbook id + verification verdict from `evidence.response`.
  - **Terminal** — final chip = the terminal disposition (only for terminal incidents).
- Only stages the incident actually reached produce a step; unreached stages are
  **omitted entirely** (no `pending` placeholder). For an in-flight incident the trace
  simply ends at the current stage. A stage that was reached but errored shows
  `outcome = errored`. (The `pending` outcome value is therefore unused in v1 and is
  dropped from the enum.)

### Frontend

- One reusable `<JourneyTrace>` component: a compact horizontal chip row, e.g.
  `Intake·anomaly → Triage real·0.82 → Enrichment malicious·0.91 → Response verified → RESOLVED`.
- Color-coded by outcome: advance = blue, resolved = green, escalated = amber,
  errored = red, pending = slate.
- Rendered on: queue rows ([IncidentQueue.tsx](../../../frontend/src/features/queue/IncidentQueue.tsx)),
  human-attention cards
  ([HumanAttentionPage.tsx](../../../frontend/src/features/attention/HumanAttentionPage.tsx)),
  and the map drawer header
  ([IncidentDrawer.tsx](../../../frontend/src/features/map/IncidentDrawer.tsx)).
- The existing detailed `EvidencePanel` stays as the drill-down (unchanged).

### Decision

Journey is **backend-derived** (single source of truth) rather than re-derived in
TypeScript, so the frontend renders a ready-made list.

---

## Thread A — Acknowledge + Resolve on escalated incidents

Two distinct operator actions on an `ESCALATED` incident.

### Acknowledge (drop from attention, keep status)

- Additive migration **0007**: nullable `acknowledged_at` (timestamp) +
  `acknowledged_by` (text) on `incidents`.
- `POST /incidents/{id}/acknowledge` (admin auth, mirrors approvals auth) sets the
  columns and writes an `audit_log` row (`action="acknowledged"`).
- The human-attention view filters out acknowledged incidents; the card/journey shows
  an "ack'd" marker.
- Status is **unchanged** (`ESCALATED` stays) — preserves single-writer purity for the
  status field.

### Resolve (true terminal close)

- New `Supervisor.close_incident(incident_id, actor)`: guarded
  `ESCALATED → RESOLVED` (expected = `ESCALATED`) with a new disposition
  `operator_resolved` + an `audit_log` row. **Supervisor stays the single writer**
  (Constitution III); the API only triggers it.
- `POST /incidents/{id}/resolve` (admin auth).
- Deliberately does **not** emit a `remediation_outcome` feedback fact (no remediation
  happened) — so manual closes do not pollute the feedback loop.
- Allowed only from `ESCALATED` (not from `AWAITING_APPROVAL`, which already has
  approve/reject). Guard returns the current disposition on a lost race (idempotent).

### Decision

Acknowledge uses **real nullable columns** (queryable queue filter) rather than scanning
`audit_log` rows.

### Frontend

- Acknowledge + Resolve buttons on escalated cards, reusing the `DecisionDialog`
  confirmation pattern from approvals
  ([DecisionDialog.tsx](../../../frontend/src/features/approvals/DecisionDialog.tsx)).

---

## Cross-cutting

- **Auth**: both new endpoints reuse the existing admin JWT / `get_current_operator`
  dependency (#12).
- **Audit**: every new state-affecting action writes an `audit_log` row.
- **Testing**: pure derivation (`journey`, verdict mapping) gets unit tests; new
  endpoints get router tests; `close_incident` / acknowledge get supervisor/repository
  tests. Run via `scripts/run-tests.sh` / `make test-*` (never one big `pytest` — OOM).
- **Eval gates**: no new gate. If `close_incident` / acknowledge dispositions need to be
  recognized, extend the existing `supervisor_routing` gate rather than adding one.

## Out of scope

- Re-opening a resolved incident.
- Bulk acknowledge/resolve.
- Any change to the response/verification path beyond what Thread C's fix requires.
- Live feeds, drift/retraining (still v3c / #18).
