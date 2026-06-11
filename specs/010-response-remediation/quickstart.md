# Quickstart — Response & Remediation Agent (#10)

How to run and verify the response stage + the human-in-the-loop approval interrupt. The forward stage and the
timeout sweeper run in the existing `worker`; the approve/reject endpoint runs in the `api`. Actions run against
**mock** executors — nothing real is isolated/disabled/blocked.

## Prerequisites

- Stack up (`docker compose up`) with `migrate` applied through **0006** (`approval_requests` + `audit_log`).
- An `LlmClient` provider configured — needed only for the **ambiguous** selection path; unambiguous incidents
  select deterministically with no LLM call. Without an LLM (or DB), `SupervisorProvider` keeps the response
  **stub**.
- The playbook catalog is present under `SENTINEL__RESPONSE__CATALOG_DIR` (default `backend/data/playbooks`).

## Configuration (typed, `extra="forbid"`)

`ResponseSettings` (section `SENTINEL__RESPONSE__*`):

| Env | Default | Meaning |
|-----|---------|---------|
| `SENTINEL__RESPONSE__AUTO_EXECUTE_ACTIONS` | `["add_to_watchlist","open_ticket","enrich_and_tag"]` | the allowlist; everything else → approval-required (**default-deny**) |
| `SENTINEL__RESPONSE__SELECT_MIN_CONFIDENCE` | `0.6` | below → ESCALATE on the LLM path |
| `SENTINEL__RESPONSE__APPROVAL_TIMEOUT_S` | `1800` | pending-approval deadline |
| `SENTINEL__RESPONSE__SWEEP_INTERVAL_S` | `60` | timeout-sweeper cadence |
| `SENTINEL__RESPONSE__CATALOG_DIR` | `backend/data/playbooks` | playbook catalog |
| `SENTINEL__RESPONSE__PROMPT_VERSION` | `v1` | pinned system prompt |

> For a live demo, set a short `APPROVAL_TIMEOUT_S` (e.g. `120`) and `SWEEP_INTERVAL_S` (e.g. `10`) so the
> timeout path is observable.

## Verify (the three milestones)

### (a) Auto-path — low-risk incident auto-remediates with an audit trail (US1)

Replay a confirmed incident whose playbook yields only allowlisted actions. Expect:

- `responding → resolved` with disposition `auto_remediated`;
- one `audit_log` row per action (`actor=response_agent`, `outcome=applied`);
- `incident.evidence["response"]` carries the `plan` + `results`;
- a deterministic selection makes **no** LLM call (the `supervisor.stage.response` span has `tokens_consumed=0`).

```bash
docker compose logs -f worker | grep -E "supervisor_transition|response"
```

### (b) Interrupt — destructive action parks for approval (US2)

Replay a confirmed incident whose playbook includes `isolate_host` (or mark evidence flags `["destructive"]`).
Expect `responding → awaiting_approval`; **nothing destructive executed**; a `pending` row in
`approval_requests` with a `deadline_at`.

```bash
curl -s localhost:8000/approvals | jq '.approvals[] | {id, incident_id, status, deadline_at}'
```

### (c) Resume — approve / reject / timeout (US2)

```bash
# approve → resumes, executes the approved action, resolves 'remediated'
curl -s -X POST localhost:8000/approvals/42/decision -d '{"decision":"approve"}' -H 'content-type: application/json' | jq
# → {"status":"resolved","disposition":"remediated"}

# reject → no execution, resolves 'rejected_by_human'
curl -s -X POST localhost:8000/approvals/43/decision -d '{"decision":"reject"}' -H 'content-type: application/json' | jq

# timeout → leave a parked incident untouched past APPROVAL_TIMEOUT_S; the sweeper expires it
#   → status escalated, disposition approval_expired, nothing executed
```

Idempotency: re-POST the same approve → `409` (already decided); the action executes once, one audit row.

## Run the tests

```bash
# unit (executors + LLM + session mocked)
uv run pytest tests/unit/test_response_select.py tests/unit/test_response_policy.py \
              tests/unit/test_response_plan.py tests/unit/test_response_park_resume.py \
              tests/unit/test_response_idempotency.py tests/unit/test_response_errors.py \
              tests/unit/test_response_boundary.py -q

# integration (real PG audit/approval + LlmClient both providers; approvals API; timeout sweeper)
uv run pytest tests/integration/test_response_provider.py tests/integration/test_approvals_api.py \
              tests/integration/test_timeout_sweeper.py -q

# e2e (auto-resolved + park→approve/reject/timeout)
uv run pytest tests/e2e/test_response_e2e.py -q

# eval — the supervisor-routing gate, now including response fixtures
uv run pytest tests/eval/test_supervisor_routing_gate.py -q
```

## What this component does NOT touch

- The `incidents` table schema (unchanged — new disposition values are plain text); the supervisor's routing
  table / step-token cap (only the reserved `resume_incident`/`expire_incident` mechanism + response
  dispositions are completed).
- Real infrastructure remediation (mock executors only), injection rails (#11), the dashboard UI (#12), and the
  §v2c verification + feedback loop (designed in the spec, implemented at the T2 checkpoint).
