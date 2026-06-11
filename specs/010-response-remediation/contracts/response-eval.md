# Contract — Eval: Supervisor-Routing Gate Extension (response fixtures)

Extends the committed, provider-independent **`supervisor-routing`** gate with response/remediation fixtures.
**No new gate** (FR-019 / Constitution II). Lives in `config/eval_thresholds.yaml` +
`tests/eval/test_supervisor_routing_gate.py`, with labelled fixtures under `tests/fixtures/`.

## What it asserts

For each labelled incident, the supervisor + response stage reach the **correct next state and disposition**:

| Fixture | Input shape | Expected status | Expected disposition |
|---------|-------------|-----------------|----------------------|
| auto-only | confirmed incident; playbook → only allowlisted actions | `resolved` | `auto_remediated` |
| destructive | confirmed incident; playbook → a destructive action | `awaiting_approval` | `awaiting_approval_destructive` |
| approve | parked incident + `approve` decision | `resolved` | `remediated` |
| reject | parked incident + `reject` decision | `resolved` | `rejected_by_human` |
| timeout | parked incident; deadline elapsed; sweeper runs | `escalated` | `approval_expired` |
| no-playbook | confirmed incident; no catalog match | `escalated` | `escalated_response` |

## Properties

- **Deterministic** — routing + the default-deny policy are pure; fixtures don't depend on LLM output (the
  ambiguous-selection LLM call is exercised separately in the **integration** tier on both providers).
- **Provider-independent** — runs identically under Gemini and Ollama; the gate is part of the day-9 freeze
  "both providers" run.
- **Thresholds** — 100% correct routing on the fixture set (routing is deterministic; any miss is a regression,
  not a tolerance).

## Out of scope for this gate

- Remediation-rationale **quality** (LLM-judge) — SPEC-eval (#13).
- §v2c verification-verdict correctness — T2.
