# Quickstart — Provider-Agnostic LLM Adapter

**Feature**: `003-llm-provider` | Verifies the seam, fallback, telemetry, readiness, and both-providers.

Prereqs: #1 (compose/Vault/config) and #2 (observability/redaction) in place. All commands from repo root;
deps via `uv`.

---

## 1. Configure the two providers

In `.env` (read by `vault-seed` on compose up):

```bash
GEMINI_API_KEY=...            # seeded into Vault at secret/llm; Ollama needs no key
```

Defaults already target the pair (override in `.env` for local non-Docker runs):

```bash
ARGUS__LLM__PRIMARY=gemini
ARGUS__LLM__FALLBACK_ORDER=["gemini","ollama"]
ARGUS__LLM__OLLAMA_BASE_URL=http://ollama:11434
```

## 2. Bring up the stack (now includes `ollama`)

```bash
docker compose up -d            # vault-seed → migrate → ollama (pulls tiny model) → api/worker
uv sync                         # local dev venv (adds google-genai, ollama)
```

`ollama` pulls a tiny pinned model on first boot (one-shot); subsequent boots reuse the volume.

## 3. Verify readiness reflects provider reachability (FR-019 / SC-010)

```bash
curl -s localhost:8000/ready | jq '.ready, .dependencies[] | select(.name=="llm")'
```

- Both reachable → `llm` healthy, `/ready` 200.
- Stop `ollama` **and** block Gemini → `llm` unhealthy, `/ready` 503 (no provider reachable).
- Bring **one** back → `/ready` returns 200 (at-least-one-reachable). Boot itself never crashed.

## 4. One uniform call through the seam (FR-002)

```python
from backend.domain.llm import LlmRequest, LlmMessage

resp = await llm.generate(                       # llm from Depends(get_llm)
    LlmRequest(messages=[LlmMessage(role="user", content="Summarize: host db01 flagged.")]),
    correlation_id="demo-1",
)
assert resp.provider in ("gemini", "ollama")     # uniform shape regardless of vendor
assert resp.model and resp.stop_reason
```

## 5. Verify automatic fallback (FR-007 / SC-003)

Force the primary to fail transiently (e.g., point `gemini` at an unreachable base or revoke network):

```python
resp = await llm.generate(req, correlation_id="demo-2")
assert resp.served_by_fallback is True
assert resp.provider == "ollama"                 # secondary served it; the call still succeeded
```

Switch the primary with **no code change** and confirm order flips:

```bash
ARGUS__LLM__PRIMARY=ollama ARGUS__LLM__FALLBACK_ORDER='["ollama","gemini"]'  # restart → ollama first
```

## 6. Verify fail-closed contract (FR-004 / SC-009)

Request structured output and force the weaker provider to serve:

```python
req = LlmRequest(messages=[...], response_schema={"type": "object", "required": ["verdict"], ...})
try:
    await llm.generate(req, correlation_id="demo-3")   # fallback can't honor the schema
except LlmError as e:
    assert e.kind == "contract_unsatisfied"            # structured error, never a degraded result
```

## 7. Verify per-call telemetry & redaction (FR-011/FR-012 / SC-004/SC-005)

Drive a call whose prompt contains a seeded fake secret, then read the trace store (#2):

```sql
SELECT name, llm_model, tokens_in, tokens_out, attributes
FROM trace_spans WHERE correlation_id = 'demo-4' AND kind = 'llm_call';
```

- The `llm_call` span carries provider, model, tokens-in/out (or `unknown`), and latency.
- The seeded secret appears **nowhere** raw in the span attributes or any log line (redacted).

## 8. Verify substitutability (SC-008) & the both-providers seed (SC-006)

```bash
# Unit tier: LLM fully mocked via Depends override — zero real provider calls
uv run pytest tests/unit -k llm

# Integration: real Ollama (Docker) + Gemini mapping (mocked HTTP always; live test skipped without key)
uv run pytest -m integration -k llm

# The seeded both-providers gate runs the minimal generate+validate check per provider
uv run pytest tests/e2e -k both_providers
```

## 9. No-bypass check (FR-001 / SC-001)

```bash
# google-genai / ollama imported ONLY in backend/infra/llm_drivers.py
grep -rn "import google\|from google\|import ollama\|from ollama" backend/ --include=*.py \
  | grep -v "backend/infra/llm_drivers.py"   # → no output
uv run lint-imports                           # import-linter contracts still pass
```

---

## Definition of done (this component)

- `uv sync` resolves; `docker compose up` comes up clean **including `ollama`**; `/ready` reflects
  at-least-one-reachable.
- A synthetic call **fails over** primary→secondary and still returns; `served_by_fallback` is observable.
- A contract-bound call **validates or raises** `CONTRACT_UNSATISFIED` (no degraded result).
- Every `llm_call` span carries provider/model/tokens/latency, redacted; no seeded secret leaks.
- Unit/integration/e2e green; the `llm_provider` both-providers gate is seeded and runs per provider;
  ≥80% coverage on new code (higher on the fail-closed validation path).
