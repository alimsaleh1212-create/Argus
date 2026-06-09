# Phase 1 — Data Model: Enrichment Agent (#9)

New types are pure Pydantic v2 in `domain/enrichment.py` (no outward imports — isolated like
`domain/triage.py`), plus one typed settings block in `infra/config.py`. Everything enrichment **reads** is an
existing contract (#5/#6); enrichment introduces **no** storage and **no** schema change.

---

## 1. `EnrichmentAssessment` (enum) — `domain/enrichment.py` (NEW)

The three-valued correlation verdict the reasoning call must return.

| Value | Meaning |
|-------|---------|
| `confirmed` | The correlated external + internal picture supports a genuine, actionable threat → proceed to response. |
| `benign` | The correlated picture indicates benign / exonerated activity (e.g. indicator now confirmed benign + known-good host history). |
| `inconclusive` | The two directions conflict or the evidence is insufficient to call confidently → abstain. |

```python
class EnrichmentAssessment(StrEnum):
    CONFIRMED = "confirmed"
    BENIGN = "benign"
    INCONCLUSIVE = "inconclusive"
```

---

## 2. `EnrichmentReport` (model) — `domain/enrichment.py` (NEW)

The structured cross-correlation produced for one incident. Validated from the LLM's structured output
(second validation layer after the response-schema); a validation failure is fail-closed → ESCALATE.

| Field | Type | Rule | Notes |
|-------|------|------|-------|
| `assessment` | `EnrichmentAssessment` | required, in-vocabulary | drives `decide_outcome`. |
| `confidence` | `float` | `0.0 ≤ x ≤ 1.0` | honest certainty; gates advance/resolve/escalate. |
| `correlation_summary` | `str` | `min_length=1` | the **headline** cross-correlation (the core deliverable). |
| `external_findings` | `list[str]` | default `[]` | corpus mappings + intel verdict statements actually used. |
| `internal_findings` | `list[str]` | default `[]` | prior-incident + time-valid-fact statements actually used. |
| `cited_evidence` | `list[str]` | `min_length≥1` | the specific items the rationale rests on (≥1). |

```python
class EnrichmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assessment: EnrichmentAssessment
    confidence: float = Field(ge=0.0, le=1.0)
    correlation_summary: str = Field(min_length=1)
    external_findings: list[str] = Field(default_factory=list)
    internal_findings: list[str] = Field(default_factory=list)
    cited_evidence: list[str] = Field(min_length=1)
```

**Validation rules (from the spec)**
- FR-004: `correlation_summary` is always present; `cited_evidence` has ≥1 item.
- SC-001: when external/internal context exists, the report carries ≥1 finding from **each direction it
  used** — enforced at the prompt + asserted in tests (not a hard schema rule, since a genuinely
  context-free incident may legitimately have empty lists with a "no corroborating context" summary).

---

## 3. `EnrichmentSettings` (settings) — `infra/config.py` (EXTEND)

Mirrors `TriageSettings`; `extra="forbid"`; registered as the `"enrichment"` section and added as
`Settings.enrichment`. `"enrichment"` is added to `_KNOWN_SENTINEL_SECTIONS`.

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `advance_min_confidence` | `float ∈ [0,1]` | `0.6` | below this → ESCALATE (abstain). |
| `resolve_min_confidence` | `float ∈ [0,1]` | `0.7` | `benign` needs ≥ this to auto-RESOLVED. |
| `corpus_k` | `int > 0` | `5` | top-k reference hits. |
| `memory_k` | `int > 0` | `5` | top-k similar prior incidents. |
| `consult_intel` | `bool` | `True` | enrichment-side toggle (intel is *also* gated by `IntelSettings.enabled`). |
| `max_indicators` | `int > 0` | `5` | cap on intel lookups + reputation `query_fact`s per incident (bounds cost). |
| `max_output_tokens` | `int > 0` | `768` | output cap for the one call (report is richer than a triage judgment). |
| `temperature` | `float ≥ 0` | `0.0` | deterministic reasoning. |
| `prompt_version` | `str` | `"v1"` | pins the system prompt. |

Validator (mirrors triage): `advance_min_confidence ≤ resolve_min_confidence` (else `ValueError` at startup).

---

## 4. `evidence_patch` shape (consumed by the supervisor, single writer)

Enrichment returns a `StageResult` (contract owned by #7) with:

```python
StageResult(
    stage=StageName.ENRICHMENT,
    outcome=<ADVANCE | RESOLVED | ESCALATE>,
    tokens_consumed=<int>,                 # reported usage → supervisor cap (SC-006)
    evidence_patch={"enrichment": report.model_dump(mode="json")},
    note="assessment=… conf=…: <correlation_summary>"[:200],
)
```

The supervisor JSONB-merges `evidence_patch` into `incident.evidence` under the `"enrichment"` key (the same
mechanism #7 already applies to `"triage"`). The dashboard (#12) and response stage (#10) read
`evidence["enrichment"]`. Enrichment **never** writes status/disposition — `decide_outcome`'s disposition is
redundant (the `ENRICHING` transition table already supplies `DISP_AUTO_RESOLVED_ENRICHMENT` /
`DISP_ESCALATED_ENRICHMENT`); `outcome` alone selects the edge.

---

## 5. Reused retrieval inputs (existing contracts — NOT redefined here)

| Contract | Source | Method enrichment calls | Returns |
|----------|--------|------------------------|---------|
| `CorpusRetriever` | `domain/corpus.py` (#5) | `search_reference(ReferenceQuery, k=corpus_k)` | `list[ReferenceHit]` (or `[]`) |
| `ThreatIntelClient` | `infra/intel.py` (#5) | `lookup(indicator, kind)` | `IntelVerdict` (verdict ∈ benign/malicious/suspicious/unknown; never raises) |
| `MemoryStore` | `domain/memory.py` (#6) | `search_similar(EpisodeQuery, k=memory_k)` | `list[MemoryHit]` (or `[]` via `NullMemory`) |
| `MemoryStore` | `domain/memory.py` (#6) | `query_fact(EntityRef, "reputation", as_of=None)` | `FactState` (`is_current`/`has_superseded`; empty via `NullMemory`) |

**Deterministic builders** (pure, in `agents/enrichment.py`, over the already-redacted
`evidence.normalized_event`):
- `build_reference_query(evidence) -> ReferenceQuery` — `technique_ids` from MITRE/rule fields,
  `terms` from `rule_description` + `rule_groups`.
- `extract_entities(evidence) -> list[EntityRef]` — `ADDRESS` (agent ip, src/dst ip), `HOST` (agent name),
  `USER` (user/srcuser/dstuser), `INDICATOR` (md5/sha*/hash/domain/url); de-duplicated; capped at
  `max_indicators` for the intel/`query_fact` calls. Mirrors `services/memory.py::_extract_entities` but
  read-only (no redactor — evidence is already redacted).

---

## 6. State / lifecycle

Enrichment is stateless per call. It does not change the incident state machine: the relevant statuses
(`ENRICHING → RESPONDING | RESOLVED | ESCALATED`) and dispositions already exist in `services/supervisor.py`
(#7). No new `IncidentStatus`, no new disposition vocabulary, **no migration**.
