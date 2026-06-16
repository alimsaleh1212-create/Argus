# Phase 1 — Data Model: Memory Feedback Loop (#16)

New types are **pure Pydantic v2** in a new `domain/feedback.py` (no outward imports — domain→domain only),
plus one config section in `infra/config.py` and an optional config-backed field on playbook catalog entries.
**No new table, no migration for M1.** The outcome fact rides the existing memory store (`MemoryStore.write_fact`,
invalidate-not-delete); the bias rides the existing grounding `Evidence` write + the deterministic router /
playbook selector. `extra="forbid"` on every model; all text is redacted before memory egress (Constitution III).

---

## 1. The remediation-outcome fact (reuses `domain/memory.TemporalFact` — no new type)

The write reuses the existing `TemporalFact` shape (`domain/memory.py`) — **no new persisted type**:

| Field | Value for the outcome fact | Notes |
|-------|----------------------------|-------|
| `entity` | `EntityRef(kind=<from action target>, value=<target>)` | **Keyed identically to the reputation fact** (`infra/intel.py::_persist_fact`) so write-key == read-key (research D2). |
| `fact_type` | `"remediation_outcome"` | NEW fact_type (config-backed `feedback.outcome_fact_type`). |
| `value` | `"verified"` \| `"unverified"` \| `"regressed"` | the incident-level `VerificationVerdict` string (from `evidence["response"]["verification"]["verdict"]`). |
| `valid_from` | incident terminal `observed_at` (`incident.updated_at`) | invalidate-not-delete supersedes a prior outcome on the same `(entity, fact_type)`. |
| `valid_until` | `None` on write | the store invalidates the prior edge on conflict. |

`query_fact(entity, "remediation_outcome", as_of=None)` → `FactState`; consumers use **`is_current`** to ignore
superseded outcomes (Constitution VI).

---

## 2. `RemediationOutcome` — `domain/feedback.py` (NEW, optional convenience enum)

```python
class RemediationOutcome(StrEnum):       # mirrors VerificationVerdict values (kept in domain/feedback to avoid
    VERIFIED = "verified"                # an agents→domain import; values are identical strings)
    UNVERIFIED = "unverified"
    REGRESSED = "regressed"

FAILURE_CLASS = frozenset({RemediationOutcome.UNVERIFIED, RemediationOutcome.REGRESSED})
```

---

## 3. `FeedbackSignal` — `domain/feedback.py` (NEW)

The read-only result of the feedback lookup for one incident (what rides `evidence["prior_outcome"]`).

| Field | Type | Rule | Notes |
|-------|------|------|-------|
| `indicator` | `str` | required | the entity the outcome applies to (redacted on surface). |
| `outcome` | `RemediationOutcome` | required | the **current** time-valid outcome value. |
| `is_current` | `bool` | required | `FactState.is_current` — superseded facts are dropped before building a signal. |
| `observed_at` | `datetime \| None` | default `None` | the fact's `valid_from` (audit/trace). |

`gather_feedback(...)` returns `list[FeedbackSignal]` (only current, only failure-class entries are bias-
relevant; `verified` is recorded for the trace but applies **no** escalation bias — FR-009).

---

## 4. Pure bias rules — `domain/feedback.py` (NEW)

```python
def has_prior_failure(signals: list[FeedbackSignal], cfg: object) -> bool:
    """True iff any current signal's outcome ∈ cfg.escalate_on (default {regressed, unverified})."""

def decide_severity_bias(severity: Severity, signals: list[FeedbackSignal], cfg: object) -> Severity:
    """Raise effective severity when a current failure-class outcome exists.
       cfg.severity_bias: 'bump_one' (one level up) | 'to_critical' | 'none'. Pure, idempotent."""

def prefer_stronger_playbook(
    candidates: list[PlaybookRef], signals: list[FeedbackSignal], cfg: object
) -> PlaybookRef | None:
    """When a current failure-class outcome exists for the target, return the highest-`strength`
       candidate (deterministic); else None (no change). Pure."""
```

All three are **pure, no I/O, fully unit-testable**, and the **only** behavior-change levers (Constitution IV).
`verified` outcomes never raise severity or change selection (FR-009).

---

## 5. `FeedbackSettings` (settings) — `infra/config.py` (NEW section)

Registered on `Settings`; `extra="forbid"`.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `enabled` | `bool` | `True` | master switch for the whole feedback loop (off → legacy v1 behavior). |
| `escalate_on` | `list[str]` | `["regressed","unverified"]` | outcome values that drive escalation bias (FR-007). |
| `severity_bias` | `Literal["bump_one","to_critical","none"]` | `"bump_one"` | how a failure-class prior raises effective severity (FR-007). |
| `prefer_stronger_playbook` | `bool` | `True` | enable the stronger-playbook preference (FR-008). |
| `max_indicators` | `int > 0` | `5` | bound on indicators re-queried per incident (cost/latency, parity with enrichment). |
| `outcome_fact_type` | `str` | `"remediation_outcome"` | the fact_type written/read (single source of truth). |

---

## 6. Playbook catalog entry — `agents/response/catalog.py` (EXTEND, config-backed)

`PlaybookEntry` gains an optional integer **`strength`** (default `0`), loaded from the playbook yaml:

```yaml
playbooks:
  - id: watchlist_only
    strength: 1
    ...
  - id: isolate_host
    strength: 3        # preferred on a known-failed prior remediation for the same target
    ...
```

Selection prefers the highest-`strength` matching candidate **only** when a current failure-class outcome exists
for the target and `feedback.prefer_stronger_playbook` is set; otherwise selection is unchanged (deterministic
match → ambiguous-tail LLM, as today).

---

## 7. Evidence slice (consumed by triage rationale; persisted by the existing grounding write)

`gather_feedback` augments the grounding `Evidence` with a redacted slice and (optionally) a biased severity:

```python
evidence["prior_outcome"] = {
    "signals": [s.model_dump(mode="json") for s in signals],   # redacted indicators
    "biased_severity": <new severity or None>,                  # provenance for the trace
}
evidence["flags"] += ["prior_failure"]    # when has_prior_failure(...) — route_grounded reads this
```

The supervisor's `route_grounded` already reads `incident.severity` + `evidence["flags"]`; the raised severity /
`prior_failure` flag drives the existing escalation routing — **no new FSM edge** (research D4).

---

## 8. Write seam — `services/memory.py` (EXTEND) + `worker.py` (EXTEND)

```python
# services/memory.py
async def record_outcome_facts(incident: Incident, store: Any, redactor: Redactor) -> None:
    """Write one time-valid remediation_outcome TemporalFact per applied target for a terminal
       incident carrying a verification verdict. Best-effort; caller wraps in try/except."""
```

`worker._maybe_record_episode` is extended (or a sibling `_maybe_record_feedback`) to call
`record_outcome_facts` alongside `record_episode` — same fire-and-forget task, same terminal guard, same redactor.

---

## 9. State / lifecycle (M1) — no FSM change

```
INGESTED → GROUNDED ──(gather_feedback: prior_failure? → bias severity/flag)──► (route_grounded escalates sooner)
   pipeline runs unchanged; supervisor remains single writer of status/disposition
   on terminal → off-path: record_episode + record_outcome_facts(verdict per applied target)
```

The only deltas are **inputs** (biased severity/flag, prior_outcome evidence) and an **off-path memory write**.
The FSM, transition table, and disposition set are **unchanged** for M1.

---

## 10. Read-side (#12, read-only — largely free)

The existing `MemoryHit` KPI DTO (`domain/dashboard.py`) + `repo.kpi_enriched_and_hit_counts()` already surface
a memory-hit count. M1 extends this with a **feedback counter** (incidents where a current prior-outcome
informed handling — derivable from `evidence["prior_outcome"]`) and surfaces the `prior_outcome` slice in the
incident trace (redacted). No new write path; the supervisor stays single writer; approve/reject stays the only
write path (Constitution III).

---

## 11. M2 (deferred, gated on #14) — feed-to-detector export

A `domain/feedback.py` export view (e.g. `DetectorIntelExport`: current confirmed-malicious indicators +
recurrence/held-remediation signals, time-valid snapshot) and a defined contract memory → #14 config. Detector
still emits the existing ingestion schema (zero downstream change). Export text passes the same guardrails as
alert text (Constitution III tiering). **Designed only — not built until #14 lands.**
