# Quickstart — Observability & Redaction

**Feature**: `002-observability-redaction` | **Date**: 2026-06-07

How to build and verify this component. The pipeline (worker/agents) does not exist yet, so e2e drives a
**synthetic incident** through the seam with seeded sensitive values. Nothing here needs a new service —
the #1 Postgres/MinIO/Vault stack is reused.

---

## Prerequisites

- The #1 foundation green (`make up` brings the stack healthy; unit tier passes).
- `uv` installed; Docker for the integration/e2e tiers.

## Add dependencies

```bash
uv add opentelemetry-sdk opentelemetry-api presidio-analyzer presidio-anonymizer spacy
# spaCy model (also added to deploy/api/Dockerfile as a build step):
uv run python -m spacy download en_core_web_sm
```

Then drop `backend/infra/redaction.py` from the coverage `omit` list in `pyproject.toml` and add
`backend/infra/tracing.py` + `observability.py` to coverage (they are now implemented, ≥80% required;
higher on the redaction safety boundary).

## Build order (internal milestones — commit each, PR ≤ ~400 lines)

1. **Redaction** — implement `Redactor` (Presidio + secret scrubber) + `RedactionPolicy` in
   `infra/redaction.py`; pure types in `domain/redaction.py`. Seed the `redaction` gate in
   `config/eval_thresholds.yaml`. → unit + integration green.
2. **Logging** — add the redaction processor + `bind_incident` correlation binding to `infra/logging.py`.
   → unit green (structured, correlated, redacted, no-context-safe).
3. **Tracing** — `infra/tracing.py` (OTel tracer, `span()` helper, token/model/latency attrs, Postgres
   `BatchSpanProcessor`/exporter); `domain/telemetry.py`; `infra/trace_repository.py`; Alembic
   `trace_spans` migration. → integration green (one tree, off-path export, migration up/down).
4. **Unified seam** — `infra/observability.py` + `ObservabilityProvider` registered in the #1 seam +
   `Depends()` providers in `dependencies.py`. → e2e + overhead measurement green.

## Verify — redaction (US1 / SC-001 / SC-006)

```bash
uv run pytest tests/unit/test_redaction.py -q
```

Confirms: seeded credentials redacted at **every** boundary (incl. `MEMORY_WRITE`/`OPERATIONAL`); PII
redacted at output boundaries; a raw IP/hostname survives at `OPERATIONAL` but is redacted at `LOG`;
nested traversal; idempotency; entropy catch; fail-closed → `[REDACTION-FAILED]`.

## Verify — logging (US2 / SC-002)

```bash
uv run pytest tests/unit/test_logging.py -q
```

Confirms: every line structured + carries the bound `correlation_id`; filtering by id returns exactly
one incident's lines; no seeded secret appears raw; a no-incident line renders with `correlation_id="-"`.

## Verify — tracing (US3 / SC-003 / SC-004)

```bash
uv run pytest -m integration tests/integration/test_tracing.py -q
```

Spins up Postgres (testcontainers), drives a synthetic incident through nested `span()` calls, then reads
`trace_spans` by `correlation_id`: one tree, no orphans; `llm_call` spans carry tokens-in/out + model +
latency (an LLM stub without usage yields `unknown`); attributes are redacted + truncated.

## Verify — off-path export resilience (SC-006)

```bash
uv run pytest -m integration tests/integration/test_export_resilience.py -q
```

Point the exporter at an unreachable DB mid-run: the synthetic incident still completes on time; the
dropped-batch counter increments; no raw content leaks.

## Verify — overhead budget (US4 / SC-005, re-checked at day-8 freeze SC-008)

```bash
uv run pytest -m e2e tests/e2e/test_overhead.py -q
```

Compares per-incident synthetic disposition time with observability fully enabled vs. minimized;
asserts the synchronous overhead is **≤ 5% (p95)** and that 100% of span export happened off-path.

## Full local gate (what CI runs)

```bash
make lint          # ruff + import-linter (incl. the no-direct-logging/otel-import guard)
uv run pytest -m "not integration and not e2e" -q     # unit
uv run pytest -m integration -q                       # integration (Docker)
uv run pytest -m e2e -q                               # e2e + overhead (Docker)
```

"Done" = all three tiers green, the `redaction` eval gate enforced in CI, overhead within budget, and the
PRs for milestones 1–4 merged.
