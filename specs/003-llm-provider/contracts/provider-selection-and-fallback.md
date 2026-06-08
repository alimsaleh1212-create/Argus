# Contract — Provider Selection, Fallback & Error Taxonomy

**Feature**: `003-llm-provider`

Defines how the adapter chooses a provider, fails over, classifies failures, and enforces the fail-closed
output contract. All behavior is **per call (stateless)** — no cross-call health state in v1 (LD3).

## Selection order

`LlmSettings.fallback_order` is the attempt order, starting with `LlmSettings.primary` (validated). Every
call begins at `fallback_order[0]`. Switching the primary is configuration-only and takes effect on
restart (FR-005, SC-002).

## Per-call algorithm (stateless)

```text
generate(request, correlation_id):
    attempts = []
    for provider in settings.fallback_order:
        attempts.append(provider)
        try:
            result = await call_with_timeout_and_retry(provider, request)   # LD8
        except LlmError as e:
            if e.kind == TRANSIENT:        # timeout / rate-limit / connection / 5xx
                continue                   # → fail over to the next provider
            raise e                        # AUTH / INVALID_REQUEST / CONTENT_REFUSAL: surface, no failover
        validate_contract(result, request) # LD4 — raises CONTRACT_UNSATISFIED (no failover, no degrade)
        result.served_by_fallback = (provider != settings.primary)
        emit_failover_counter_if(result.served_by_fallback)
        return result
    raise LlmError(kind=EXHAUSTED, provider=None, attempts=attempts)   # FR-010
```

Notes:
- **Transient → fail over** to the next provider in order; **non-retryable → surface immediately** (a bad
  primary credential must not silently shift all load to the secondary — FR-008).
- A **timeout** is `TRANSIENT` (eligible for failover); a slow provider never hangs the path (FR-009).
- `served_by_fallback` and a failover counter make the event observable, not silent (edge case).

## Timeout & retry (per provider attempt)

Each provider attempt is wrapped with a per-call **timeout** (`request_timeout_s`) and a **transient-only**
bounded retry (`tenacity`, `max_retries`, exponential backoff). Retries apply **only** to `TRANSIENT`
failures; `AUTH`/`INVALID_REQUEST`/`CONTENT_REFUSAL` are never retried (LD8).

## Error taxonomy (`LlmErrorKind`)

| Kind | Trigger | Retry? | Fail over? | Surfaced as |
|------|---------|--------|-----------|-------------|
| `TRANSIENT` | timeout, 429, connection error, 5xx | yes (bounded) | yes | (internal; only surfaces as `EXHAUSTED`) |
| `AUTH` | 401/403, bad key | no | no | `LlmError(AUTH)` |
| `INVALID_REQUEST` | 400, context-window exceeded | no | no | `LlmError(INVALID_REQUEST)` |
| `CONTENT_REFUSAL` | provider safety refusal | no | no | `LlmError(CONTENT_REFUSAL)` — caller branchable |
| `CONTRACT_UNSATISFIED` | result fails required schema/tool validation | no | no | `LlmError(CONTRACT_UNSATISFIED)` |
| `EXHAUSTED` | whole order failed transiently | — | — | `LlmError(EXHAUSTED, attempts=[…])` |

Each driver maps its vendor's errors into this taxonomy; messages are **secret-free** (name the condition,
never the value).

## Fail-closed output contract (LD4 / FR-004 / SC-009)

When `request.response_schema` and/or `request.tools`/`require_tool` are set, the adapter **validates the
result after the call**:

- structured output → the content MUST parse and validate against `response_schema`;
- required tool → the response MUST contain the demanded tool call with JSON-parseable arguments.

If validation fails (common when a weaker local fallback can't honor the contract), the adapter raises
`LlmError(CONTRACT_UNSATISFIED)` — it **never** returns a silently degraded result. The supervisor (#7)
treats this as a step failure and escalates (HITL-consistent). `ProviderCapability` informs request
*shaping* only; it does **not** pre-skip a provider (capability-matched routing was rejected — LD4).

## Guarantees

- **SC-003**: under an induced transient failure of the active provider, the call still succeeds via the
  fallback; no incident fails solely because one provider is down.
- **SC-009**: 0 silently degraded results — every contract-bound call validates or raises.
- Deterministic: same config + same failure pattern ⇒ same attempt order and outcome (testable without
  real providers by injecting driver fakes that raise specific `LlmErrorKind`s).
