# Contract — Structured Logging

**Feature**: `002-observability-redaction` | Consumed by: every component (all logging goes through
`get_logger`).

Builds on the #1 `configure_logging` chain (`merge_contextvars` + `add_log_level` + `TimeStamper` +
JSON render). This spec inserts the **redaction processor** and the **correlation-id** binding.

---

## Log record shape (every line)

```json
{
  "event": "triage_completed",
  "level": "info",
  "timestamp": "2026-06-07T12:00:00Z",
  "correlation_id": "inc_01J...",      // the incident id, or "-" outside an incident (FR-011)
  "trace_id": "inc_01J...",            // == correlation_id when in an incident
  "component": "backend.agents.triage",
  "...": "domain fields — all redacted"
}
```

## Guarantees

1. **Structured** (FR-008): JSON key/value, never free-form.
2. **Correlated** (FR-009): `correlation_id` is bound in `structlog.contextvars` by `bind_incident(id)`
   and is consistent across the worker and all three agents for one incident; filtering by it returns
   exactly that incident's lines (SC-002).
3. **Redacted-before-emit** (FR-010): the redaction processor runs in the shared chain before the JSON
   renderer; **no logging path bypasses it**. Credentials + PII scrubbed at the `LOG` boundary.
4. **No-context safety** (FR-011): a line emitted with no incident bound is still structured and carries
   `correlation_id="-"` (or `no_incident=true`); it never raises.
5. **Cheap / on-path**: JSON-to-stdout is synchronous but negligible; the heavy export (spans, eval) is
   off-path (see span-trace-schema). This keeps the SC-005 budget.

## Processor order (in `configure_logging`)

```
merge_contextvars  →  add_log_level  →  TimeStamper  →  StackInfoRenderer
                   →  redaction_processor   (NEW — secret scrubber + PII at LOG boundary, fail-closed)
                   →  JSONRenderer
```

## Contract tests (must exist)

- A line emitted inside `bind_incident("inc1")` carries `correlation_id="inc1"`; filtering returns only
  inc1's lines (unit).
- A seeded secret/PII passed as a log field never appears raw in the rendered output (unit).
- The redaction processor raising internally drops the offending field (fail-closed), not the whole line,
  and never emits raw (unit).
- A line emitted with no incident bound renders with `correlation_id="-"` and no error (unit).
