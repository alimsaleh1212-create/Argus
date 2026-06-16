# Contract — Feedback Consumption & Bias (#16, M1)

**Owner**: `services/feedback.gather_feedback` (read-only) + pure rules in `domain/feedback.py` + two consumers
(`route_grounded` severity/routing, `selection.select_playbook` stronger-playbook). **Deterministic, no LLM.**

---

## 1. The lookup — `services/feedback.gather_feedback`

```
async def gather_feedback(*, memory, entities, cfg) -> list[FeedbackSignal]
```

- Runs at the **grounded boundary in the worker** — after `ground()`, before `route_grounded` (research D3).
- For each indicator entity (bounded by `cfg.feedback.max_indicators`), concurrently
  `query_fact(entity, cfg.feedback.outcome_fact_type, as_of=None)` wrapped in `_safe(...)` (best-effort).
- **Read-key MUST equal the write-key** (the same `EntityRef` construction the write uses — D2).
- Keeps only `FactState.is_current` results (superseded outcomes are ignored — Constitution VI, FR-012).
- Returns `list[FeedbackSignal]`. Memory absent/outage → `[]` (no bias; baseline v1 behavior — FR-003/D8).

## 2. Severity / routing escalation bias (FR-007)

```
biased = decide_severity_bias(evidence.severity, signals, cfg)   # pure
if has_prior_failure(signals, cfg):
    evidence.flags += ["prior_failure"]
    evidence.severity = biased            # 'bump_one' (default) | 'to_critical' | 'none'
    evidence["prior_outcome"] = {...}      # redacted signals + provenance (trace)
```

- `route_grounded` already routes on `incident.severity` + `evidence["flags"]`; the raised severity / flag
  drives the existing **critical/ambiguous → escalate** path. **No new `StageOutcome`, no new FSM edge.**
- `verified` (success) signals apply **no** bias (FR-009).

## 3. Stronger-playbook preference (FR-008)

```
chosen = prefer_stronger_playbook(candidates, signals_for_target, cfg)   # pure
```

- In `select_playbook`, when the action target has a **current** failure-class outcome and
  `cfg.feedback.prefer_stronger_playbook` is set, choose the highest-`strength` matching candidate **before**
  any ambiguous-tail LLM call (deterministic).
- Single match / no stronger candidate → **unchanged** selection (best-effort).
- A stronger playbook with destructive actions still parks for approval via the existing #10 policy
  (Constitution V — no new auto authority).

## 4. Single-writer honesty (Constitution III — FR-011)

- Feedback writes **only** to the grounding `Evidence` input (persisted by the existing `set_grounded` write the
  worker already performs pre-pipeline) and to the **memory store** (the write contract).
- The **supervisor remains the sole writer of `status`/`disposition`** — it routes on the biased input.

## 5. Determinism & degradation

- All bias is via the pure functions in `domain/feedback.py`; **no LLM call** on the feedback path
  (Constitution IV). The triage LLM may *see* `evidence["prior_outcome"]` for its rationale but does **not**
  decide the bias.
- Any error in `gather_feedback` → `[]` → baseline behavior (fail-open, FR-003).
