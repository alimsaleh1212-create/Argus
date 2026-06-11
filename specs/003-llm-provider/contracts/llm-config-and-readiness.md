# Contract — Configuration, Secrets, Readiness & Eval Seed

**Feature**: `003-llm-provider`

## Settings section (`ARGUS__LLM__*`, `extra="forbid"`)

`LlmSettings` is registered on `Settings` and `"llm"` added to `_KNOWN_ARGUS_SECTIONS`. Fields and
defaults are in [data-model.md](../data-model.md) §Configuration. Env example:

```bash
ARGUS__LLM__PRIMARY=gemini
ARGUS__LLM__FALLBACK_ORDER=["gemini","ollama"]
ARGUS__LLM__REQUEST_TIMEOUT_S=30.0
ARGUS__LLM__MAX_RETRIES=2
ARGUS__LLM__GEMINI_MODEL=<small-fast-gemini>     # exact id in DECISIONS.md
ARGUS__LLM__GEMINI_VAULT_PATH=secret/llm
ARGUS__LLM__OLLAMA_BASE_URL=http://ollama:11434
ARGUS__LLM__OLLAMA_MODEL=<tiny-model>            # exact id in DECISIONS.md
```

## Secrets (Vault, #1)

- The **Gemini API key** is resolved from Vault at `gemini_vault_path` at startup — never a settings field,
  never logged (FR-015). The path is added to `vault.required_paths` so a missing key **fails boot fast**
  with a secret-free error (SC-007).
- `.env.example` gains a `GEMINI_API_KEY` (a.k.a. `GOOGLE_API_KEY`) placeholder seeded into Vault by
  `vault-seed` on `docker compose up` (replacing the legacy `OPENAI_API_KEY` placeholder). **Ollama needs
  no credential.**

## Compose: the `ollama` service (the one infra addition)

`compose.yaml` gains an `ollama` service (official `ollama/ollama` image) exposing `:11434`, with a
one-shot init that **pulls a tiny pinned model** so the fallback path, the integration tier, and the
both-providers eval are real and the stack comes up clean from a fresh clone. The API/worker reach it at
`ARGUS__LLM__OLLAMA_BASE_URL`. No app Dockerfile change (the official image is used directly).

## Readiness: at-least-one-reachable (LD5 / FR-019 / SC-010)

`backend/infra/health.py` gains:

```python
async def check_llm(settings, container) -> DependencyStatus:
    """healthy iff >=1 configured provider is reachable (bounded by dependency_timeout_s)."""
    # probe each provider cheaply (e.g., Gemini models endpoint / Ollama /api/tags);
    # name="llm", healthy = any(reachable); detail names which are down (secret-free).
```

It is added to `run_readiness_probes`. Because `/ready` aggregates with `all(d.healthy)`, an `llm`
dependency that is healthy iff **≥1** provider responds yields the at-least-one-reachable gate: `/ready`
is 503 only when **no** provider is reachable, 200 once one is. **Reachability never crashes boot** — only
config/credential errors do (FR-015). `/health` (liveness) is unaffected.

## Provider registration & DI (LD10)

- `register_llm_provider()` (in `llm.py`) appends `LlmProvider` (`name="llm"`) to the registry; it is
  invoked in `create_app()`'s provider-registration step **after** the observability provider.
- `LlmProvider.build(settings)` constructs both drivers once (resolving the Gemini key from Vault) and
  disposes them on shutdown; `get_llm()` (in `dependencies.py`) returns `app.state.container.llm`.

## Eval seed (FR-018 / SC-006)

`config/eval_thresholds.yaml` gains a seeded **`llm_provider`** gate: a minimal generate-and-validate check
that must run to completion **against each configured provider independently** (Gemini and Ollama), so a
regression on either fails CI. The full harness is owned by #13; this seeds the gate so it is wired from
day one and the "passes on both providers" constitution rule is enforceable.

## No-bypass guard (FR-001)

Extend the existing convention (#2): `google-genai` and `ollama` are imported **only** in
`backend/infra/llm_drivers.py`. The layered `import-linter` contract already prevents non-infra layers from
reaching infra internals; code review + the ruff import rules keep vendor SDKs out of
routers/services/agents/repositories/domain.
