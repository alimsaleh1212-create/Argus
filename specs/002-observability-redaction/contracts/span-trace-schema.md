# Contract — Span / Trace-Tree Schema & Trace Store

**Feature**: `002-observability-redaction` | Consumed by: dashboard trace inspector + KPIs (#12),
eval/temporal gates (#13), and the supervisor's step/token cap (#7, reads per-span tokens).

The persisted `trace_spans` table is the **queryable store of record**. Its shape is a stable contract;
later specs read it but do not change its meaning.

---

## Span (one unit of work)

| Field | Type | Contract |
|-------|------|----------|
| `span_id` | str | unique |
| `trace_id` | str | **equals the incident correlation id**; roots the per-incident tree |
| `parent_span_id` | str \| null | null only for the incident root |
| `correlation_id` | str | indexed; the incident/request id (OD3) |
| `name` | str | stable, low-cardinality (`triage.step`, `tool.<name>`, `retrieval.memory`, `llm.call`) |
| `kind` | enum | `root` \| `agent_step` \| `tool_call` \| `retrieval` \| `llm_call` |
| `status` | enum | `OK` \| `ERROR` \| `UNSET`; `ERROR` ⇒ redacted `error_message` |
| `started_at` / `ended_at` | timestamptz | UTC; `latency_ms` derived |
| `llm_model` | str \| null | required on `llm_call` |
| `tokens_in` / `tokens_out` | int \| null | `null` rendered as **`unknown`** (FR-013, SC-004) |
| `attributes` | jsonb | **redacted** (TRACE boundary), truncated to `span_attr_max_bytes` (FR-017) |

## Trace tree (derived on read)

- `GET trace by correlation_id` → all spans with that id; assemble by `parent_span_id`.
- **Invariants** (SC-003): exactly one `root` per incident; every non-root parent resolves within the
  same `trace_id`; **no orphaned or duplicated spans**.

## Per-incident telemetry (derived — the KPI shape #12 reads)

```
total_tokens_in   = Σ tokens_in   (known only)
total_tokens_out  = Σ tokens_out  (known only)
end_to_end_ms     = root.latency_ms
step_count        = count(spans where kind != root)
error_steps       = count(spans where status = ERROR)
```

## Export contract (off-path)

- Spans are queued to a `BatchSpanProcessor` and written by a custom exporter into `trace_spans` on a
  background task (OD2/OD7). **Export never blocks or fails an incident** (SC-006); a failed flush
  increments a dropped-batch counter and is logged (redacted).
- On clean shutdown, the processor **force-flushes** (FR-019).

## Truncation (FR-017)

- Any `attributes` value exceeding `span_attr_max_bytes` is truncated with a `…[truncated]` marker.
- Truncation runs **after** redaction so it can never re-expose a partially-redacted secret.

## Contract tests (must exist)

- One synthetic incident with nested steps → exactly one tree, no orphans (integration).
- `llm_call` span persists tokens-in/out + model; a provider without usage yields `unknown` (integration).
- Oversized attribute is truncated and contains no raw sensitive substring (unit).
- `trace_spans` migration applies to an empty DB and rolls back cleanly (integration; #1 SC-006 style).
