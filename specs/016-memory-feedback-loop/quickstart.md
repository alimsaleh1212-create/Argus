# Quickstart — Memory Feedback Loop (#16, M1)

Backend-only extension (no new service, no new image, no migration). Runs on the existing worker
(`python -m backend.worker`). M1 = within-SOAR tuning; M2 (feed-to-detector) is deferred until #14.

## Prerequisites

- The v1 stack up (`docker compose up`) — Postgres, Redis, Neo4j/Graphiti (or the pgvector fallback).
- #15 merged (verification produces `evidence["response"]["verification"]`). ✓ (merged)

## Run

```bash
# Settings (FeedbackSettings, extra="forbid") — defaults are on:
#   ARGUS_FEEDBACK__ENABLED=true
#   ARGUS_FEEDBACK__ESCALATE_ON=["regressed","unverified"]
#   ARGUS_FEEDBACK__SEVERITY_BIAS=bump_one
#   ARGUS_FEEDBACK__PREFER_STRONGER_PLAYBOOK=true
#   ARGUS_FEEDBACK__MAX_INDICATORS=5
#   ARGUS_FEEDBACK__OUTCOME_FACT_TYPE=remediation_outcome

# The worker writes the outcome fact off-path on terminal, and reads it at the grounded boundary.
python -m backend.worker
```

## Test (three-tier — never one bare `pytest`; spaCy/Graphiti OOM)

```bash
scripts/run-tests.sh unit          # pure bias rules, fact builder/mapping, FeedbackSettings
scripts/run-tests.sh integration   # write→query_fact round-trip; bias against a seeded fact
scripts/run-tests.sh e2e           # 1st vs 2nd occurrence handled differently (the demo)
# or: make test-unit / test-integration / test-e2e
```

## Eval

```bash
python -m backend.eval --gate feedback         # deterministic, provider-independent
python -m backend.eval --gate temporal_memory  # incl. new remediation_outcome_flip case
python -m backend.eval --gate supervisor_routing  # incl. new prior_regressed_escalates fixture
```

The `feedback` gate (yaml block + registered runner, added together — orphan check is a hard error) asserts a
repeat of a known-failed indicator escalates sooner / picks a stronger playbook, and that verified/superseded
priors apply no change.

## Demo (brief #5 — "same alert handled differently after memory accumulates")

1. **First occurrence.** Ingest an alert on indicator X; let it run to a `regressed`/`unverified` terminal
   (verification finds the remediation did not hold). Off-path, a `remediation_outcome=regressed` fact is
   written for X (time-valid).
2. **Second occurrence.** Ingest a *similar* alert on X. At the grounded boundary the feedback lookup finds the
   current `regressed` fact → effective severity is bumped and `prior_failure` is flagged → the incident
   **escalates sooner**, and (if multiple playbooks match) response **prefers the stronger playbook**.
3. **Dashboard.** The incident trace shows the redacted `prior_outcome` that informed handling; the
   feedback/memory-hit KPI ticks up. (Read-only — supervisor stays single writer.)
4. **Time-validity.** A later `verified` outcome for X supersedes the `regressed` one (invalidate-not-delete);
   `query_fact(as_of=<earlier>)` still returns `regressed`, `query_fact(now)` returns `verified`.

## Degradation check

- Stop Neo4j/memory → ingest a repeat: **no bias** (baseline v1 behavior), **no write**, incident still reaches
  a terminal/escalated state. Verification that the loop is an enhancement, never a single point of failure.

## What is NOT here (M2, gated on #14)

- Exporting memory-derived intel to the detector (feed-to-detector). Designed in
  [data-model.md](data-model.md) §11 / [research.md](research.md) D11; built only after #14 lands.
