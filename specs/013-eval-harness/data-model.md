# Phase 1 Data Model — Evaluation Harness (#13)

Pure DTOs live in `backend/domain/eval.py` (domain-isolated, I/O-free, Pydantic v2,
`model_config = {"extra": "forbid"}`). They describe the **report**, not persistence — the eval system
reads existing fixtures and writes only the report JSON to MinIO. No DB migration.

## Enums

### `GateProviderDim`
`provider_independent` · `per_provider` — whether a gate is scored once or once per LLM provider.

### `GateKind`
`required` · `reported_only` — `required` blocks CI; `reported_only` records a score and only blocks at
the catastrophic floor (the rationale gate).

### `RunMode`
`per_pr` · `nightly` · `freeze` — selects the provider set and whether the report uploads to MinIO.

### `FreezeVerdict`
`certifiable` · `not_certifiable` · `incomplete` — overall outcome. `incomplete` = a required dimension
could not complete (provider outage) or the report failed to persist.

### `RationaleLabel`
`grounded` · `partially_grounded` · `ungrounded` — the judge's ordinal score per rationale (R2).

## Entities

### `GateSpec` (parsed from `config/eval_thresholds.yaml`)
| Field | Type | Notes |
|---|---|---|
| `name` | `str` | e.g. `triage`, `retrieval`, `rationale` — the registry key |
| `description` | `str` | from the yaml |
| `kind` | `GateKind` | derived from `required: true/false` (rationale = `reported_only`) |
| `provider_dim` | `GateProviderDim` | `per_provider` iff the yaml lists `providers`/`check_per_provider` |
| `threshold` | `dict[str, Any]` | the raw threshold block (gate-specific keys) |
| `providers` | `list[str]` | for per-provider gates |

**Validation**: every `GateSpec.name` MUST resolve to a registered runner (orphan check); every runner
MUST have a `GateSpec` (stale check). Failure → harness aborts non-zero before scoring (FR-002).

### `GateResult`
| Field | Type | Notes |
|---|---|---|
| `gate` | `str` | gate name |
| `kind` | `GateKind` | required / reported_only |
| `provider` | `str \| None` | `None` for provider-independent gates |
| `score` | `float \| dict[str, float]` | scalar (e.g. macro-F1) or sub-scores (e.g. hit@k + MRR) |
| `threshold` | `dict[str, Any]` | the compared threshold |
| `passed` | `bool \| None` | `None` = could not evaluate (recorded "unknown", FR-016) |
| `blocking` | `bool` | `kind == required` (or rationale floor breach) |
| `evidence` | `str` | short human-readable basis (counts, failing fixture id) — **redacted** |

### `ProviderResult`
Groups a per-provider gate's `GateResult`s: `{ provider: str, gates: list[GateResult] }`.

### `RationaleScore` (sub-entity of the rationale `GateResult`)
| Field | Type | Notes |
|---|---|---|
| `stage` | `str` | `triage` / `enrichment` / `response` |
| `producer_provider` | `str` | which provider produced the rationales scored |
| `grounded_rate` | `float` | fraction scored `grounded` (or `grounded`+½·`partially`) |
| `judge_human_agreement` | `float` | exact-match rate of judge vs hand-labels (R2) |
| `n` | `int` | sample size |

### `RationaleJudgeSample` (fixture; `tests/fixtures/rationale/{stage}.json`)
`{ incident_context, rationale_text, human_label: RationaleLabel, cites_supplied_evidence: bool }` — the
hand-labeled reference the judge is validated against.

### `EvalReport` (root document → `eval_report.json`)
| Field | Type | Notes |
|---|---|---|
| `schema_version` | `str` | `"1"` |
| `run_id` | `str` | uuid4 per run |
| `run_mode` | `RunMode` | per_pr / nightly / freeze |
| `commit_sha` | `str` | evaluated commit |
| `git_tag` | `str \| None` | set on freeze tag runs |
| `created_at` | `datetime` | UTC |
| `providers` | `list[str]` | provider set this run exercised |
| `gate_results` | `list[GateResult]` | flat list across providers |
| `rationale` | `list[RationaleScore] \| None` | present at freeze/nightly only |
| `verdict` | `FreezeVerdict` | aggregate outcome |
| `summary` | `dict[str, int]` | counts: passed / failed / reported / unknown |

**Aggregation rule** → `verdict`:
- any **required** `GateResult.passed == False` → `not_certifiable`;
- any **required** dimension with `passed is None` (provider/store outage) at freeze/nightly, or a failed
  MinIO upload → `incomplete`;
- a `reported_only` breach of the **catastrophic floor** is promoted to blocking → `not_certifiable`;
- otherwise → `certifiable`.

## Configuration — `EvalSettings` (added to `backend/infra/config.py`)
`pydantic-settings`, `extra="forbid"`, mounted on root `Settings` as `eval`.

| Field | Default | Notes |
|---|---|---|
| `thresholds_path` | `"config/eval_thresholds.yaml"` | source of truth (R1) |
| `report_bucket` | `"eval-reports"` | already in `MinioSettings.buckets` |
| `report_prefix` | `"reports"` | key `reports/{commit}/{run_id}.json` (R7) |
| `freeze_prefix` | `"freezes"` | freeze copy `freezes/{tag}/eval_report.json` |
| `providers_per_pr` | `["ollama"]` | single local provider (R5) |
| `providers_freeze` | `["gemini", "ollama"]` | both-providers matrix (R6) |
| `judge_provider` | `"gemini"` | pinned judge (R6) |
| `rationale_fixture_dir` | `"tests/fixtures/rationale"` | hand-labeled set (R4) |

The per-gate **numeric** thresholds (including the rationale `catastrophic_floor`) stay in the yaml, not
`EvalSettings` — `EvalSettings` is *wiring*, the yaml is *capability bars* (clean separation, R1).

## What this model does NOT add
- No DB table / Alembic migration (eval reads existing fixtures + writes a blob).
- No memory write, no de-redaction path, no incident/approval mutation.
- No red-team / injection entities (VD1 — seam reserved only).
