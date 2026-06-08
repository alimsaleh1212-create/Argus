# Phase 1 — Data Model: Triage Agent

**Component**: #8 `SPEC-triage-agent` · **Date**: 2026-06-08

Triage introduces **two pure types** (`domain/triage.py`), **one config block** (`config.py`), and **one
small persistence extension** (`advance_status` gains an `evidence_patch`). It defines **no new table and no
migration** — the judgment is stored inside the existing `incidents.evidence` JSONB column.

---

## 1. `TriageVerdict` (enum) — `backend/domain/triage.py`

The closed real/noise/uncertain vocabulary. Any value outside this set is an out-of-vocabulary failure →
fail-closed escalate (FR-007).

| Value | Meaning |
|-------|---------|
| `real` | Real and actionable given the supplied evidence → candidate to **advance** |
| `noise` | False positive / benign → candidate to **auto-resolve** |
| `uncertain` | Evidence insufficient to call → **escalate** (abstain) |

---

## 2. `TriageJudgment` (model) — `backend/domain/triage.py`

The structured assessment triage produces for one incident. Pure Pydantic v2 (`extra="forbid"`); may import
`Severity` from `domain/incident.py` (domain→domain is allowed).

| Field | Type | Rules |
|-------|------|-------|
| `verdict` | `TriageVerdict` | required; enum-validated (OOV → fail-closed) |
| `confidence` | `float` | required; `0.0 ≤ confidence ≤ 1.0` |
| `assessed_severity` | `Severity \| None` | optional; triage's own read; **never** overwrites canonical severity (FR-012) |
| `rationale` | `str` | required; non-empty (`min_length=1`); plain-language, already-redacted |
| `cited_evidence` | `list[str]` | required; **≥1 item** (`min_length=1`) — names the evidence relied on (FR-002) |

**Validation rules**
- `confidence` outside `[0,1]` → `ValidationError` → fail-closed escalate.
- `cited_evidence` empty → `ValidationError` (FR-002 requires ≥1 cited item).
- Unknown `verdict` string → enum failure → fail-closed escalate.
- This is the **second** validation layer; the first is the adapter's `response_schema` check (required
  fields present + JSON parses). Both must pass before any non-escalate outcome (FR-007).

**Serialization**: `judgment.model_dump(mode="json")` becomes `evidence_patch["triage"]` (§5).

---

## 3. `TriageSettings` (config) — `backend/infra/config.py`

New typed section; env prefix `SENTINEL__TRIAGE__*`. Add `"triage"` to `_KNOWN_SENTINEL_SECTIONS` and a
`triage: TriageSettings = Field(default_factory=TriageSettings)` field on `Settings`.

| Field | Type | Default | Rules |
|-------|------|---------|-------|
| `advance_min_confidence` | `float` | `0.6` | `0 ≤ x ≤ 1`; floor for any confident call (below ⇒ abstain) |
| `resolve_min_confidence` | `float` | `0.7` | `0 ≤ x ≤ 1`; higher bar to auto-close noise (asymmetric blast radius) |
| `max_output_tokens` | `int` | `512` | `> 0`; output budget for the single call |
| `temperature` | `float` | `0.0` | `≥ 0`; deterministic classification |
| `prompt_version` | `str` | `"v1"` | pins the system prompt the eval runs against |

**Cross-field rule** (`model_validator`): `advance_min_confidence ≤ resolve_min_confidence` — auto-close can
never be *easier* than advance (enforces the TD3 asymmetry; misconfiguration fails at boot).

---

## 4. Outcome mapping (pure) — `decide_outcome(judgment, cfg)`

`backend/agents/triage.py`, pure (no I/O), unit-tested independent of the LLM. Returns
`tuple[StageOutcome, str | None]` (outcome, disposition). Evaluated **top to bottom**:

```text
1. judgment.verdict == UNCERTAIN                          -> (ESCALATE, "escalated_triage")
2. judgment.confidence <  cfg.advance_min_confidence      -> (ESCALATE, "escalated_triage")
3. judgment.verdict == REAL  (conf >= advance_min)        -> (ADVANCE,  None)
4. judgment.verdict == NOISE and conf >= resolve_min      -> (RESOLVED, "auto_resolved_triage")
5. judgment.verdict == NOISE and conf <  resolve_min      -> (ESCALATE, "escalated_triage")
```

Boundary: `>=` passes, `<` abstains (spec edge case — deterministic & testable). Dispositions reuse the
existing supervisor vocabulary (`DISP_AUTO_RESOLVED_TRIAGE`, `DISP_ESCALATED_TRIAGE` in `services/supervisor.py`).
For `ADVANCE` the table disposition is `None` (the transition table carries it).

---

## 5. `StageResult` produced by triage (existing type, `domain/pipeline.py`)

Triage returns the existing `StageResult` — no change to the type:

| Field | Triage value |
|-------|--------------|
| `stage` | `StageName.TRIAGE` |
| `outcome` | from `decide_outcome` — `ADVANCE` \| `RESOLVED` \| `ESCALATE` (never `NEEDS_APPROVAL`) |
| `tokens_consumed` | `usage.prompt_tokens + usage.completion_tokens` (0-safe if a provider reports `None`) |
| `disposition` | from `decide_outcome` (used by the supervisor only where the transition table has none) |
| `evidence_patch` | `{"triage": judgment.model_dump(mode="json")}` |
| `note` | short redacted rationale preview (≤200 chars) for the trace |

Triage emits **only** `ADVANCE`/`RESOLVED`/`ESCALATE` (FR-003). Any other state→outcome pairing the
supervisor would reject as an illegal transition (`escalated_illegal_transition`) — a structural guard.

---

## 6. Persistence extension — `IncidentRepository.advance_status(..., evidence_patch=None)`

The **only** schema/persistence change, scoped by the spec (FR-010, TD8). Single writer preserved.

**Signature**: add a keyword-only `evidence_patch: dict[str, Any] | None = None`.

**Behavior**: when `evidence_patch` is provided, the guarded `UPDATE` also merges it into `evidence`:

```sql
UPDATE incidents
SET status = :target,
    disposition = :disposition,          -- only when provided (existing branch)
    evidence = COALESCE(evidence, '{}'::jsonb) || :evidence_patch::jsonb,
    updated_at = :now
WHERE id = :id AND status = :expected
RETURNING id;
```

- The `WHERE status = :expected` guard is unchanged → still atomic, idempotent, single-writer.
- `||` is a shallow JSONB merge: `evidence.triage` is set/replaced; sibling keys (`verdict`, `severity`,
  `normalized_event`, `summary`, `retrieved_context`, `flags`) are untouched.
- `evidence_patch=None` ⇒ behavior identical to today (back-compatible; the supervisor's existing
  fast-path/cap/error transitions pass nothing and are unaffected).
- **No migration**: `evidence` JSONB already exists (#4 `0003`).

**Supervisor call site** (`services/supervisor.py`): in the in-flight stage loop, pass
`evidence_patch=result.evidence_patch` into the post-stage `advance_status(...)`. No transition-table change.

---

## 7. Entity relationships

```text
Incident (incidents row, owned by #4; written only by repo via supervisor)
└── evidence  (JSONB, owned by #4 grounding)
    ├── verdict / severity / normalized_event / summary / retrieved_context / flags   (grounding, #4)
    └── triage  ◄── merged by supervisor from StageResult.evidence_patch  (THIS COMPONENT)
        └── TriageJudgment { verdict, confidence, assessed_severity, rationale, cited_evidence }

TriageSettings (config) ──► decide_outcome ──► StageOutcome ──► supervisor transition table ──► status + disposition
LlmClient (#3) ──► one generate(response_schema=TriageJudgment schema) ──► validate ──► TriageJudgment
```

**Ownership recap**: triage *produces* `TriageJudgment` and the `StageResult`; the **supervisor** persists
everything (status, disposition, merged evidence). Triage holds no session and no action client and writes
nothing — the structural Constitution III boundary, unchanged.
