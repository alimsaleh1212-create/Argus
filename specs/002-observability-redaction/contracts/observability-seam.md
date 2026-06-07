# Contract — The Observability Seam (logger + tracer + redactor)

**Feature**: `002-observability-redaction` | Consumed by: **every** later component that logs, traces,
prompts a model, or stores/displays content (#4, #6, #7, #8, #9, #10, #11, #12, #13).

This is the spec's central outward contract: the **one seam** through which all observable output and
all sensitive-data exits flow. The binding rule (FR-018): **no component logs, traces, builds a prompt,
or stores/displays content through any path that bypasses this seam.**

---

## What the seam exposes (`backend/infra/observability.py`)

```python
def get_logger(name: str) -> BoundLogger: ...        # structlog, redaction processor in-chain (OD8)

@asynccontextmanager
async def span(name: str, *, kind: str, **attributes) -> AsyncIterator[Span]: ...
    # opens an OTel span nested under the current context; attributes are redacted (TRACE) before set

def record_llm_usage(span, usage) -> None: ...       # sets tokens_in/out, model, latency (OD9)

def get_redactor() -> Redactor: ...                  # the configured singleton (Presidio + scrubber)

def bind_incident(correlation_id: str) -> None: ...  # binds id into structlog + trace context (OD3)
```

### `Depends()` providers (`backend/dependencies.py`)

```python
async def get_obs(request) -> Observability: ...        # bundle: logger factory + tracer + redactor
async def get_redactor(request) -> Redactor: ...
async def get_tracer(request) -> Tracer: ...
```

- Resources are read from `app.state.container` (the #1 seam) — **never** module globals (FR-018/020).
- In tests, `app.dependency_overrides[get_redactor] = fake` substitutes a double (FR-020).

## Lifespan & registration

- An **`ObservabilityProvider`** (`name="observability"`) is appended to the #1 provider registry. Its
  `build()` constructs the **Presidio engine + secret scrubber once**, the OTel `TracerProvider` with a
  `BatchSpanProcessor` → Postgres exporter, and the structlog redaction processor; on context exit it
  **force-flushes** spans (FR-019).
- Registration order places `observability` **after** `db_engine` (the exporter needs the session
  factory) and before agent/pipeline providers.

## Guarantees (testable)

1. Every `get_logger(...)` line passes through redaction; there is no raw logging path (FR-010).
2. `span(...)` nests correctly under the active context; all spans for one incident share `trace_id ==
   correlation_id` and form one tree with no orphans (FR-012, SC-003).
3. `record_llm_usage` sets tokens-in/out + model + latency, or marks `unknown` (FR-013, SC-004).
4. The redactor is a **singleton** (Presidio model loaded once) — verified by identity across calls.
5. Span export is **off the synchronous path** (BatchSpanProcessor); an unreachable exporter never
   fails or delays an incident (FR-015, SC-006).
6. On shutdown, buffered spans are flushed (FR-019).

## Contract tests (must exist)

- A handler/tool acquiring `get_obs` emits a line + a span that share the correlation id (integration).
- Overriding `get_redactor` with a fake redactor changes behaviour without editing the consumer (unit).
- Killing the Postgres exporter mid-run: incidents still complete; dropped-batch counter increments
  (integration).
- A bypass attempt (constructing a raw `logging.getLogger`) is caught by an `import-linter` / lint rule
  guarding direct `logging`/`opentelemetry` imports outside `infra/` (unit/CI).
