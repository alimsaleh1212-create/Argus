# Quickstart — Triage Agent (#8)

How to build, run, and verify triage. Triage replaces the supervisor's `run_triage` stub with one real LLM
call; it runs inside the existing `worker`. **No new service, dependency, or migration.**

## Prerequisites

- Components #1–#5/#7 in place (platform, observability, LLM provider, ingestion, supervisor).
- `GEMINI_API_KEY` in `.env` (seeded into Vault by `vault-seed`); Ollama service up for the fallback/both-
  providers eval.
- `uv` environment synced: `uv sync`.

## What gets built/changed

| File | Change |
|------|--------|
| `backend/domain/triage.py` | **new** — `TriageVerdict`, `TriageJudgment` |
| `backend/agents/triage.py` | **replace stub** — `make_triage_handler(llm, cfg)`, prompt, one-call generate, validate, `decide_outcome`, error mapping |
| `backend/infra/config.py` | **extend** — `TriageSettings`, register `"triage"`, add `Settings.triage` |
| `backend/infra/supervisor_provider.py` | **extend** — wire real triage handler from `container.llm` + `settings.triage` |
| `backend/services/supervisor.py` | **extend (small)** — pass `result.evidence_patch` to `advance_status` |
| `backend/repositories/incidents.py` | **extend (small)** — `advance_status(evidence_patch=…)` JSONB-merge |
| `backend/worker.py` | **extend** — `register_llm_provider()` before `SupervisorProvider` |
| `config/eval_thresholds.yaml` | **extend** — activate the `triage` gate |

## Run the tests (three tiers + eval)

```bash
# Unit — fast, LLM mocked (mapping, validation, fail-closed, no-state, one-call)
uv run pytest tests/unit/test_triage_judgment.py tests/unit/test_triage_decide.py \
              tests/unit/test_triage_errors.py -q

# Integration — handler against a real LlmClient/provider (Docker)
uv run pytest -m integration tests/integration/test_triage_provider.py -q

# E2E — ambiguous incident through worker→supervisor→triage (LLM faked at the driver boundary)
uv run pytest -m e2e tests/e2e -q

# Eval gate — macro-F1 on the committed labeled set, BOTH providers
uv run pytest tests/eval/test_triage_gate.py -q
```

## Manual verification (the three behaviors)

With a **fake `LlmClient`** you can drive each path deterministically:

1. **Real → advance** — fake returns `{"verdict":"real","confidence":0.9,"rationale":"…cites rule_id…",
   "cited_evidence":["rule_description"]}`. Run an ambiguous (medium) incident through the supervisor:
   expect `status=enriching`, and `evidence.triage.verdict == "real"` persisted (single writer).
2. **Noise → auto-resolve** — fake returns `noise`, `confidence=0.85`. Expect `status=resolved`,
   `disposition=auto_resolved_triage`, and **no enrichment/response stage ran** (adaptive depth, FR-014).
3. **Uncertain / low-confidence → escalate** — fake returns `uncertain` (or `confidence=0.4`). Expect
   `status=escalated`, `disposition=escalated_triage`, with the rationale recorded.

### Failure injection (fail-closed, SC-005)

- Fake raises `LlmError(TRANSIENT)` → supervisor retries (`max_stage_retries`), then `escalated`; worker
  keeps consuming.
- Fake returns malformed JSON / `{"verdict":"banana"}` → `ToolError(malformed_output)` → `escalated`
  (never `resolved`/`enriching`).
- Confirm `tokens_consumed > 0` is reported and feeds the supervisor cap; confirm exactly **one**
  `llm.generate` call per incident.

## Config knobs (`SENTINEL__TRIAGE__*`)

```bash
SENTINEL__TRIAGE__ADVANCE_MIN_CONFIDENCE=0.6   # below ⇒ abstain/escalate
SENTINEL__TRIAGE__RESOLVE_MIN_CONFIDENCE=0.7   # higher bar to auto-close noise (must be ≥ advance)
SENTINEL__TRIAGE__MAX_OUTPUT_TOKENS=512
SENTINEL__TRIAGE__TEMPERATURE=0.0
SENTINEL__TRIAGE__PROMPT_VERSION=v1
```

Changing `RESOLVE_MIN_CONFIDENCE` shifts the resolve/escalate boundary **without touching reasoning code**
(FR-004) — a good thing to demonstrate.

## Done criteria (Constitution I/II)

- Unit + integration + e2e green; ≥80% on new code (higher on the fail-closed paths).
- `triage` eval gate green on **both** providers.
- `ruff` + `import-linter` clean (note: `backend/agents/*` and `backend/domain/triage.py` come off the
  coverage `omit` list once implemented).
- Committed and pushed behind a focused PR (≤ ~400 lines).
