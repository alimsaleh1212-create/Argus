# Contract — The LLM Seam (DI)

**Feature**: `003-llm-provider` | Consumed by #6, #7, #8, #9, #10, #13

The single way any component calls a model. Vendor SDKs are confined to `backend/infra/llm_drivers.py`;
no other module imports `google-genai`/`ollama` (no-bypass, FR-001).

## Obtaining the seam

```python
# backend/dependencies.py
async def get_llm(request: Request) -> "LlmClient":
    """Return the process-singleton LLM adapter (FR-014)."""
    return request.app.state.container.llm
```

Consumers depend on it via FastAPI `Depends(get_llm)` (or receive it from the supervisor, which got it the
same way). They **never** construct a vendor client and **never** import a vendor SDK (FR-001, SC-001).
Substitutable in tests: `app.dependency_overrides[get_llm] = lambda: FakeLlm()` (FR-016, SC-008).

## The call

```python
class LlmClient:
    async def generate(
        self,
        request: LlmRequest,
        *,
        correlation_id: str,
        parent_span_id: str | None = None,
    ) -> LlmResponse: ...
```

- **Input** `LlmRequest` and **output** `LlmResponse` are the uniform shapes from
  [data-model.md](../data-model.md) — identical regardless of which provider serves the call (FR-002).
- `correlation_id` ties the call's span to the incident (consumes #2's contextvar-bound id).
- Raises `LlmError` (never a vendor exception) on failure; see
  [provider-selection-and-fallback.md](./provider-selection-and-fallback.md).

## Tool-scoped clients (FR-003 — the structural boundary)

The caller passes only the `ToolSpec`s it is permitted (in `LlmRequest.tools`); the adapter forwards
exactly that set to the provider. A caller given an empty/read-only tool set **cannot** emit an action
tool call — the capability is absent by construction, not discouraged by prompt. (Per-role tool sets are
owned by #8; this seam provides the mechanism.)

## What every call does (wrapping, not re-implementing #2)

For each `generate()` the adapter:

1. **Scrubs credentials from the outbound prompt** via the #2 `Redactor` (CREDENTIAL class) — a raw secret
   in alert text is never transmitted to a provider (FR-006a defense-in-depth). It does **not** strip
   operational identifiers the agent needs (LD7).
2. Opens an **`LLM_CALL` span** via #2's `span(tracer, name, SpanKind.LLM_CALL, correlation_id, parent_span_id, attrs=…)`.
3. Runs selection + stateless fallback (separate contract) to get a result.
4. Records usage via #2's **`record_llm_usage(span, response.usage, response.model)`** — provider/model/
   tokens/latency land on the span; missing counts render "unknown" (FR-011, SC-004).
5. Lets the span record the (redacted-at-TRACE) prompt/completion attributes; any log line goes through
   #2's log redaction processor (FR-012, SC-005). No raw secret/PII reaches a log/trace/snapshot.

The adapter **does not** re-implement tracing or redaction — it consumes the #2 seam.

## Response guarantees

- `response.provider` / `response.model` identify what actually served the call; `served_by_fallback` is
  `True` when a non-primary provider answered (FR-007, telemetry/eval attribution).
- If a structured-output/tool contract was requested, a returned `LlmResponse` has **validated** against it
  (otherwise the call raised `LlmError(CONTRACT_UNSATISFIED)` — fail-closed, SC-009).

## Non-goals

No streaming (v1), no embeddings (owned by #6), no prompt/agent logic. The seam transports calls; it does
not decide what to ask.
