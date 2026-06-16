# Contract — Feedback-Effectiveness Eval Gate (#16, M1)

**Owner**: `backend/eval/gates/feedback.py` + a `feedback` block in `config/eval_thresholds.yaml`, added in the
**same change** (the declared⇔registered orphan/stale check is a hard error — #13). **Deterministic /
provider-independent**, like `supervisor_routing` and `verification`.

---

## 1. What it proves

That **behavior changes after memory accumulates** (roadmap §3 016): the 2nd occurrence of a known-failed
indicator is escalated sooner and/or selects a stronger playbook than the 1st (baseline). Because the bias is
deterministic, the gate is provider-independent and 100%-pass-able; the **baseline-vs-repeat delta is also the
end-to-end proof of the write-key == read-key invariant** (research D2).

## 2. Fixtures — `tests/fixtures/feedback/*.json`

Each fixture is a **baseline-vs-repeat pair** of grounded-incident inputs + the prior outcome to seed:

```json
{
  "name": "prior_regressed_escalates",
  "severity": "medium",
  "seed_outcome": {"indicator": "<target>", "value": "regressed", "is_current": true},
  "baseline": {"expected_route": "ambiguous"},
  "repeat":   {"expected_route": "critical_or_escalated", "expect_severity_bumped": true}
}
```

A second family covers playbook strength:

```json
{
  "name": "prior_failure_picks_stronger_playbook",
  "candidates": [{"id": "watchlist_only", "strength": 1}, {"id": "isolate_host", "strength": 3}],
  "seed_outcome": {"indicator": "<target>", "value": "regressed", "is_current": true},
  "baseline": {"expected_playbook": "watchlist_only"},
  "repeat":   {"expected_playbook": "isolate_host"}
}
```

`verified`/superseded seeds MUST yield **no** delta (negative fixtures — FR-009/FR-012).

## 3. Runner — `run_feedback(spec, provider=None) -> GateResult`

- Drives each fixture's baseline and repeat through the **pure bias rules** (`decide_severity_bias`,
  `has_prior_failure`, `prefer_stronger_playbook`) + `route_grounded`.
- Scores the fraction of fixtures whose repeat shows the expected change **and** whose baseline shows none.
- Registers itself: `GATE_REGISTRY["feedback"] = run_feedback`.
- Missing fixture dir / no files → `passed=None` with evidence (same shape as `verification`).

## 4. Thresholds — `config/eval_thresholds.yaml`

```yaml
feedback:
  description: >
    Feedback-effectiveness gate (SPEC-memory-feedback-loop #16). Deterministic / provider-independent.
    Proves a 2nd occurrence of a known-failed indicator escalates sooner / picks a stronger playbook than
    the baseline, and that verified/superseded priors apply no change. Extends temporal_memory (outcome-fact
    time-validity) and supervisor_routing (prior-regressed-escalates); redaction covers the outcome fact + KPI.
  required: true
  threshold:
    pass_rate: 1.0
  fixtures:
    - prior_regressed_escalates
    - prior_unverified_escalates
    - prior_failure_picks_stronger_playbook
    - verified_prior_no_change
    - superseded_prior_no_change
```

## 5. Extensions (not duplicates)

- **`temporal_memory`** — add a `remediation_outcome_flip` case (write `verified`@t1, `regressed`@t2 → assert
  `query_fact(as_of=t1)=verified` superseded, `query_fact(now)=regressed`, verified RETAINED).
- **`supervisor_routing`** — add a `prior_regressed_escalates` fixture (grounded incident + seeded prior →
  escalation route).
- **`redaction`** — the existing `memory_write` + `dashboard` boundaries already cover the outcome fact + the
  feedback KPI view; **no new boundary**.

## 6. Constitution

II (eval-gated, both providers run the suite before tier freeze; this gate is provider-independent), IV
(deterministic), VI (time-validity case).
