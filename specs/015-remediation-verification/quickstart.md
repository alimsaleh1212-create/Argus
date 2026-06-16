# Quickstart — Remediation Verification (#15, M1)

Backend-only, no migration, no new service. Verification runs at the **tail of the response stage**; the
stack and bring-up are unchanged from #10/#12.

## Prerequisites

- The existing turnkey stack (`docker compose up` — `vault-seed` + `migrate` run before `api`).
- `uv` venv at repo root (Python 3.12).

## Run the tests (never one bare `pytest` — spaCy/Graphiti OOM)

```bash
scripts/run-tests.sh unit          # pure decide_verdict, probe contract, settings
scripts/run-tests.sh integration   # re-check vs real Redis/Postgres/memory; handler verdict paths
scripts/run-tests.sh e2e           # one full incident → verdict → disposition
# or: make test-unit / test-integration / test-e2e
```

## Run the verification eval gate

```bash
scripts/run-evals.sh verification          # deterministic, provider-independent (one gate per subprocess)
python -m backend.eval --gate verification # equivalent direct entrypoint
```

The gate scores `verified/unverified/regressed` on `tests/fixtures/verification/` against
`min_accuracy` / `max_false_verified_rate: 0.0` in `config/eval_thresholds.yaml`.

## What "done" looks like (Constitution I)

M1 is done when, on **both** the auto path and the human-approved path:

1. A remediation whose indicator re-checks clean and whose probe is `expected` → verdict `verified`,
   disposition unchanged (`auto_remediated`/`remediated`).
2. A remediation whose indicator is still `malicious` (or probe `unexpected`) → verdict `regressed`,
   disposition `remediation_unverified`, incident **escalated** (not resolved).
3. An inconclusive/unknown signal → verdict `unverified`, escalated (fail-closed; SC-004).
4. A memory/intel/probe outage → still terminates the incident (verification never blocks disposition;
   SC-005), verdict `unverified`.
5. Re-running the worker on the terminal incident changes nothing (idempotent; SC-006).
6. The verdict is visible (redacted) in the dashboard trace; `remediation_unverified` is distinguishable in
   the queue (SC-007).
7. `verification` gate green; `temporal_memory` + `redaction` + `supervisor_routing` extensions green; all
   three test tiers green in CI.

## Demo (T2)

- Drive a remediated incident whose indicator the seeded memory shows **still malicious** → the trace shows
  `verdict=regressed` and the incident escalates as `remediation_unverified` instead of falsely resolving.
- Pair with #16's "same alert handled differently after memory accumulates" for the full T2 moment.

## Milestone PRs (each ≤ ~400 lines)

- **M1-a** verdict core — `domain/response.py` types + `decide_verdict` + `ProbeState`/`probe()` on the
  protocol + mock `probe()` + `build_regressed_executors` + unit tests.
- **M1-b** wiring — `verify_remediation` in `agents/response.py` (both passes), `StageOutcome.UNVERIFIED`,
  the supervisor edge, `ResponseSettings` fields, integration + e2e tests.
- **M1-c** gate + surface — `backend/eval/gates/verification.py` + yaml block + fixtures; temporal/redaction/
  routing extensions; read-only dashboard verdict surface.
- **M2** (later, gated on #14) — `IncidentStatus.VERIFYING` + monitoring-loop park/resume + dwell sweeper.
