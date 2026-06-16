# Contract: Verification-Accuracy Eval Gate (#15)

A new **`verification`** gate, added to **both** `config/eval_thresholds.yaml` and the gate registry
(`backend/eval/gates/verification.py`) in the **same change** — the harness (#13) enforces a
declared⇔registered **orphan/stale check as a hard error**.

---

## Gate semantics

- **Deterministic, provider-independent** (like `supervisor_routing`): drives a committed labeled fixture
  set of post-remediation states through `decide_verdict` and scores classification accuracy. 100%-pass is
  achievable because the verdict logic is deterministic; the threshold is committed (not hardcoded).
- If `verify_llm_tiebreak` is enabled in a run, the gate gains an LLM dimension and MUST pass on **both**
  providers (Constitution II); default-off keeps it provider-independent.

## Threshold block (`config/eval_thresholds.yaml`)

```yaml
  verification:
    description: >
      Remediation-verification accuracy gate (SPEC-remediation-verification #15). Drives a labeled set of
      post-remediation states (probe state + indicator re-check) through decide_verdict and scores the
      verified/unverified/regressed classification. Deterministic / provider-independent (no LLM on the
      default path). Extends — does not duplicate — the temporal-memory gate (verification fact
      time-validity) and the redaction gate (verification record + dashboard view).
    required: true
    threshold:
      min_accuracy: 0.95
      max_false_verified_rate: 0.0   # never label an unverified/regressed state "verified" (SC-003/SC-004)
    fixtures:
      - verified_clean_indicator
      - regressed_indicator_still_malicious
      - regressed_probe_unexpected
      - unverified_intel_unknown
      - unverified_probe_inconclusive
      - conflict_probe_ok_indicator_malicious   # deterministic worst-case → regressed
      - multi_action_worst_case                 # one regressed action dominates
```

`max_false_verified_rate: 0.0` is the load-bearing safety invariant — it operationalizes "0 false-success
claims" (SC-003) and "fail closed to unverified" (SC-004).

## Registry entry

`backend/eval/gates/verification.py` exposes `async def run_verification(spec, provider=None) -> GateResult`
and registers in `GATE_REGISTRY` under `"verification"`, mirroring `run_supervisor_routing`. Fixtures live in
`tests/fixtures/verification/`.

## Extensions to existing gates (extend, don't duplicate)

- **temporal_memory**: add a case asserting the verification fact's time-validity is queried correctly
  (current vs superseded) via `query_fact(as_of=…)`.
- **redaction**: the verification record (targets/details) + the dashboard verdict view are covered by the
  existing boundary set (`memory_write`, `dashboard`, `operational`) — no new boundary, add seeded-secret
  coverage to the verification path.
- **supervisor_routing**: add a fixture pair `verified_resolves` / `unverified_escalates` exercising the new
  `(RESPONDING, UNVERIFIED)` edge.

## Out of scope for this gate

Feedback-effectiveness (that 2nd-occurrence behaviour changes) is **#16's** gate, not this one. This gate
scores only the verdict classification.
