# Phase 1 Data Model — Provider-Agnostic LLM Adapter

**Feature**: `003-llm-provider` | **Date**: 2026-06-08

Pure types live in `backend/domain/llm.py` (no outward deps, Pydantic v2) — they satisfy the
domain-isolation `import-linter` contract and are importable by any layer. The adapter is **stateless**:
**no DB table, no Alembic migration, no persisted entity**. The one configuration shape (`LlmSettings`)
lives in `backend/infra/config.py`. Nothing here is incident/business logic — these are the uniform
shapes the seam produces/consumes.

---

## Enumerations (`backend/domain/llm.py`)

### `ProviderId`
| Value | Meaning |
|-------|---------|
| `GEMINI` (`"gemini"`) | Google Gemini — cloud, the default primary |
| `OLLAMA` (`"ollama"`) | Local Ollama runtime — the default fallback |

> Open set by design: a third provider can be added here + a driver without changing consumers.

### `StopReason`
| Value | Meaning |
|-------|---------|
| `END_TURN` | model finished normally |
| `MAX_TOKENS` | hit the output cap |
| `TOOL_USE` | model is requesting a tool call |
| `CONTENT_FILTER` | provider safety filter stopped generation |
| `ERROR` | call failed (carried on the error path, not a successful response) |
| `UNKNOWN` | provider gave no mappable reason |

### `LlmErrorKind`
| Value | Retryable? | Fails over? | Meaning |
|-------|-----------|-------------|---------|
| `TRANSIENT` | yes (bounded) | yes | timeout, rate limit, connection error, 5xx |
| `AUTH` | no | no | authentication / permission failure — surfaced immediately |
| `INVALID_REQUEST` | no | no | malformed request / context-window exceeded |
| `CONTENT_REFUSAL` | no | no | model refused on safety grounds (distinct, caller-branchable) |
| `CONTRACT_UNSATISFIED` | no | no | result failed required structured-output/tool validation (LD4, fail-closed) |
| `EXHAUSTED` | yes (whole call) | — | every provider in the order failed; single structured error |

---

## Models (`backend/domain/llm.py`, Pydantic v2)

### `LlmMessage`
| Field | Type | Notes |
|-------|------|-------|
| `role` | `Literal["system","user","assistant","tool"]` | conversation role |
| `content` | `str` | message text (already-redacted/assembled by the caller) |
| `tool_call_id` | `str \| None` | set on `tool` messages returning a tool result |
| `name` | `str \| None` | tool name for `tool` messages |

### `ToolSpec`
| Field | Type | Notes |
|-------|------|-------|
| `name` | `str` | tool identifier |
| `description` | `str` | what the tool does |
| `parameters` | `dict` | JSON Schema for the tool's arguments |

> The set of `ToolSpec`s a caller is handed **is** the structural capability boundary (FR-003): a triage
> client is built with an empty/read-only tool set and thus cannot emit an action tool call.

### `LlmRequest`
| Field | Type | Notes |
|-------|------|-------|
| `messages` | `list[LlmMessage]` | non-empty; the conversation |
| `system` | `str \| None` | optional system instruction |
| `tools` | `list[ToolSpec]` = `[]` | permitted tools (scoped by the caller) |
| `response_schema` | `dict \| None` | JSON Schema the response MUST validate against (structured output) |
| `require_tool` | `str \| bool \| None` | force a/the named tool call (maps to vendor tool_choice) |
| `max_tokens` | `int \| None` | output cap |
| `temperature` | `float \| None` | low-variance/determinism lever (FR-017); `None` = provider default |
| `top_p` | `float \| None` | optional; callers set at most one of temperature/top_p |

**Validation**: `messages` non-empty; if `response_schema` is set it must be a valid JSON-Schema object;
`temperature`/`top_p` within provider-valid ranges when present.

### `TokenUsage`
| Field | Type | Notes |
|-------|------|-------|
| `prompt_tokens` | `int \| None` | `None` ⇒ "unknown" (provider omitted it) |
| `completion_tokens` | `int \| None` | `None` ⇒ "unknown" |

> **Contract with #2**: the field names match what `backend/infra/tracing.record_llm_usage(span, usage,
> model)` reads via `getattr` (`prompt_tokens` / `completion_tokens`), so usage flows into the existing
> span telemetry with no change to #2 (LD6). Drivers normalize vendor usage into this shape:
> Gemini `prompt_token_count`/`candidates_token_count`; Ollama `prompt_eval_count`/`eval_count`.

### `ToolCall`
| Field | Type | Notes |
|-------|------|-------|
| `id` | `str` | provider tool-call id (for the follow-up `tool` message) |
| `name` | `str` | tool requested |
| `arguments` | `dict` | parsed arguments (always JSON-parsed, never raw string) |

### `LlmResponse`
| Field | Type | Notes |
|-------|------|-------|
| `content` | `str` | produced text (`""` when the turn is purely tool calls) |
| `tool_calls` | `list[ToolCall]` = `[]` | requested tool calls, if any |
| `usage` | `TokenUsage` | normalized counts |
| `model` | `str` | concrete model id that served the call |
| `provider` | `ProviderId` | which backend served it |
| `stop_reason` | `StopReason` | why generation stopped |
| `served_by_fallback` | `bool` | `True` when a non-primary provider served it (telemetry/eval attribution) |

### `ProviderCapability`
| Field | Type | Notes |
|-------|------|-------|
| `provider` | `ProviderId` | |
| `supports_tools` | `bool` | native tool/function-calling |
| `supports_structured_output` | `bool` | native JSON-schema-constrained generation |
| `reports_token_usage` | `bool` | returns usage counts |

> Used for **request shaping and telemetry only**, never for routing (LD4 chose fail-closed validation,
> not capability-matched routing). Drives how a driver maps the request and what "unknown" usage to
> expect; the post-call validation is what actually enforces the contract.

### `LlmError` (domain exception)
| Field | Type | Notes |
|-------|------|-------|
| `kind` | `LlmErrorKind` | classification driving retry/fallback/surface |
| `provider` | `ProviderId \| None` | which provider produced it (`None` for `EXHAUSTED`) |
| `message` | `str` | secret-free, names the condition not the value |
| `attempts` | `list[ProviderId]` = `[]` | providers tried (for `EXHAUSTED`/telemetry) |

> Raised by the adapter; the supervisor (#7) maps it to a `ToolError`-style retryable/terminal step
> outcome. `CONTRACT_UNSATISFIED` and `EXHAUSTED` are terminal-for-this-call; `CONTENT_REFUSAL` is a
> branchable outcome, not a crash.

---

## Configuration — `LlmSettings` (`backend/infra/config.py`)

A new typed section (`extra="forbid"`), registered on `Settings` and added to
`_KNOWN_ARGUS_SECTIONS` as `"llm"`. Env vars: `ARGUS__LLM__<FIELD>`.

| Field | Type / default | Notes |
|-------|----------------|-------|
| `primary` | `ProviderId` = `GEMINI` | env-selected primary (SC-002) |
| `fallback_order` | `list[ProviderId]` = `[GEMINI, OLLAMA]` | full attempt order; must start with `primary` and be non-empty |
| `request_timeout_s` | `float` > 0 = `30.0` | per-call timeout (LD8) |
| `max_retries` | `int` ≥ 0 = `2` | transient-only bounded retry per provider (LD8) |
| `gemini_model` | `str` = (small fast Gemini; exact id in `DECISIONS.md`) | concrete primary model |
| `gemini_vault_path` | `str` = `"secret/llm"` | Vault KV path holding the Gemini API key (added to `vault.required_paths`) |
| `ollama_base_url` | `str` = `"http://ollama:11434"` | the compose `ollama` service |
| `ollama_model` | `str` = (tiny model, e.g. ~0.5–1B; exact id in `DECISIONS.md`) | concrete fallback model |

**Validation**: `fallback_order` non-empty and `fallback_order[0] == primary`; `request_timeout_s > 0`;
`max_retries >= 0`; model ids non-empty. The Gemini key is **never** a settings field — it is resolved
from Vault at startup (FR-015); a required-but-missing key fails boot.

---

## State & lifecycle

- **No persistent state.** The only per-call "state" is the in-memory attempt sequence (primary →
  fallback order), recomputed every call (stateless, LD3).
- **Lifecycle**: `LlmProvider.build(settings)` constructs both driver clients once (resolving the Gemini
  key from Vault), yields the `LlmClient`, and disposes the clients on shutdown (LD10). Built after the
  observability provider so the adapter can read the `Observability` bundle from the container.

## Relationship to other components' data

- Produces `LlmResponse.usage` in the exact shape **#2**'s `record_llm_usage` consumes (LD6).
- Consumes the **#2** `Redactor` + `span()` for the model-boundary redaction/telemetry (LD7) — no new
  telemetry entity is defined here.
- The uniform `LlmRequest`/`LlmResponse` are the contract the **supervisor (#7)** and **agents (#8/#9/#10)**
  build on; the `ToolSpec` scoping is the mechanism #8 uses for the triage no-action-tools boundary.
