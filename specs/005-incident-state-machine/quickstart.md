# Quickstart — Incident State Machine (Supervisor)

**Component**: #7 `SPEC-incident-state-machine`. Watch a grounded incident get **driven to a disposition**:
the fast-path, the full ambiguous depth, a cap breach, and a stage error. No new services — the supervisor
runs in the existing `worker` container, with **no LLM call**.

## 0. Bring the stack up (unchanged from #4)

```bash
docker compose up -d            # vault-seed + migrate run first; api + worker start; migration 0004 applies
docker compose logs -f worker   # the supervisor logs each transition (redacted)
```

## 1. Obvious noise → auto-resolved with ZERO stage calls (fast-path, SC-003)

```bash
# A low-severity Wazuh alert (rule.level 0–3 ⇒ severity=low)
curl -sS -X POST localhost:8080/ingest/wazuh \
  -H "Authorization: Bearer $INGEST_TOKEN" -H "Content-Type: application/json" \
  -d @tests/fixtures/wazuh_alerts/low_noise.json
```

Then verify the worker drove it straight to terminal with no agent involved:

```bash
# status → resolved, disposition → auto_resolved_noise
docker compose exec postgres psql -U sentinel -c \
  "SELECT status, disposition FROM incidents ORDER BY created_at DESC LIMIT 1;"
# trace tree shows the supervisor span only — no triage/enrichment/response child spans
```

## 2. Ambiguous → full depth triage → enrichment → response (the spine, SC-001)

```bash
curl -sS -X POST localhost:8080/ingest/wazuh \
  -H "Authorization: Bearer $INGEST_TOKEN" -H "Content-Type: application/json" \
  -d @tests/fixtures/wazuh_alerts/medium_ambiguous.json
```

With #7's stub handlers, the incident walks `grounded → triaging → enriching → responding → resolved`
(`auto_remediated`). The trace tree (dashboard #12 later) shows three child stage spans. This is the
end-to-end pipeline **before** the real agents (#8–#10) exist.

## 3. Obvious critical → straight to response (fast-path)

A `rule.level 12–15` alert (`severity=critical`) skips triage/enrichment: `grounded → responding → …`.
With a destructive-flagged fixture the response stub returns `NEEDS_APPROVAL` and the incident **parks**:

```bash
docker compose exec postgres psql -U sentinel -c \
  "SELECT status, disposition FROM incidents WHERE status='awaiting_approval';"
# awaiting_approval / awaiting_approval_destructive  — resume mechanism arrives with #10
```

## 4. Graceful degradation (SC-002 / SC-004) — run the tests

The fault paths are exercised in tests rather than by hand:

```bash
uv run pytest tests/unit/test_supervisor_routing.py -q     # fast-path, adaptive depth, illegal-transition guard
uv run pytest tests/unit/test_supervisor_bounds.py -q      # step cap + token cap → escalated, never loops
uv run pytest tests/unit/test_supervisor_errors.py -q      # retryable retried; non-retryable → escalated; worker survives
uv run pytest tests/integration/test_supervisor_pg.py -q   # guarded transitions, disposition persistence, resume from in-flight
uv run pytest tests/e2e/test_pipeline_dispositions.py -q   # POST alert → worker → terminal disposition across fixtures
```

## 5. The eval gate (SC, Constitution II)

```bash
uv run pytest tests/eval/test_supervisor_routing_gate.py -q   # 100% of routing fixtures reach the expected next stage
```

## What "done" looks like

- A grounded incident **always** reaches exactly one of `resolved` / `escalated` / `failed` (or parks in
  `awaiting_approval`) — never stuck in-flight.
- Obvious noise/critical resolve via the fast-path with **no stage call**; ambiguous ones walk the full
  depth.
- A cap breach or a stage error lands the incident in `escalated` **without crashing the worker**.
- Re-POSTing / re-delivering an incident is idempotent; an in-flight incident resumes.
- The supervisor imports **no** LLM client; the routing eval gate is green.
- Unit + integration + e2e green in CI; PR ≤ ~400 lines across the three milestones.
