# Incident Journey Tags + Escalation Actions + Enrichment Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make enrichment + graph-RAG actually run in the demo, show each incident's stage-by-stage journey on its card, and give operators an Acknowledge and a Resolve action on escalated incidents.

**Architecture:** Three independently-shippable milestones built in dependency order — **C** (root-cause + fix enrichment/graph-RAG so there's real data to show), then **B** (backend-derived journey DTO + a reusable frontend trace component), then **A** (additive ack columns + a single new supervisor close edge + two POST endpoints + card buttons). The supervisor stays the single writer of status/disposition; acknowledge only sets metadata columns.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy + Alembic, Pydantic v2, pytest; React 19 + TypeScript + TanStack Query + Vite + Tailwind (Node 20).

## Global Constraints

- Run tests via `scripts/run-tests.sh` / `make test-*` — **never** one big `pytest` (spaCy + graphiti OOM). Run a single test file/node by passing its path to the script.
- Supervisor remains the **single writer** of `status`/`disposition` (Constitution III). The resolve action goes through a new `Supervisor.close_incident`; the API only triggers it.
- New DB schema is **additive only** (nullable columns); migration revision **0007**, `down_revision = "0006"`.
- Pure domain types have **no outward imports** (domain-isolation import-linter contract).
- Enrichment retrieval stays **best-effort**: a retrieval miss must never error the stage (only the single LLM call is fatal).
- Every new state-affecting action writes an `audit_log` row.
- New endpoints reuse the existing admin auth (`get_current_operator`); the `incidents` router is already mounted behind it.
- No new eval gate. Register any new disposition in `pipeline_view` mappings so KPI/terminal rollups stay correct.

---

## Milestone C — Fix enrichment + graph-RAG (diagnose, then fix)

This milestone is **investigation-gated**: Task C1 produces runtime evidence that decides which of C2 / C3 (or both) to apply. Both fix tasks are written out concretely so whichever the diagnosis selects is ready to apply.

### Task C1: Reproduce and diagnose the enrichment stage error

**Files:**
- Create: `docs/superpowers/plans/C1-enrichment-findings.md` (scratch findings — not committed to product code)

**Interfaces:**
- Produces: a findings file stating the exact exception class + message the supervisor converts to `escalated_stage_error`, and which layer failed (stage LLM parse vs. graph-RAG retrieval).

- [ ] **Step 1: Bring the stack up and confirm providers**

Run:
```bash
docker compose up -d postgres redis neo4j ollama vault minio
docker compose run --rm migrate
docker compose up -d api worker
docker compose ps
```
Expected: `api`, `worker`, `neo4j`, `ollama` all `Up`/healthy.

- [ ] **Step 2: Fire the two enrichment demo cases and capture worker logs**

Run:
```bash
bash scripts/demo_full_workflow.sh 2>&1 | tee /tmp/demo-run.log || true
docker compose logs worker --since=5m 2>&1 | grep -iE "enrich|escalat|malformed|llm_|memory|graphiti|traceback|error" | tail -80
```
Expected: at least one `supervisor_stage_error stage=enrichment ...` line. Record the `kind=` value (`llm_*` vs `malformed_output`) and any Graphiti/Neo4j retrieval error.

- [ ] **Step 3: Classify the failure into the findings file**

Write `docs/superpowers/plans/C1-enrichment-findings.md` recording, with copied log lines:
- The `ToolError.kind` that escalated the stage (decides **C2**).
- Whether `search_similar`/`query_fact` returned empty or raised (decides **C3**), and whether a Gemini key is configured (`docker compose exec api printenv | grep -i gemini` — absent ⇒ Ollama path).

- [ ] **Step 4: Decide which fix tasks apply**

In the findings file, mark each of C2 / C3 as **APPLY** or **SKIP** with one sentence of justification. Commit the findings file:
```bash
git add docs/superpowers/plans/C1-enrichment-findings.md
git commit -m "chore(enrichment): record demo enrichment-stage-error diagnosis"
```

### Task C2: Harden enrichment structured-output parsing for the fallback provider

> Apply only if C1 found `kind=malformed_output` (or an `llm_*` parse failure) on the enrichment stage.

**Files:**
- Modify: `backend/agents/enrichment/reasoning.py` (function `report_from_response`)
- Test: `tests/unit/enrichment/test_reasoning_parse.py`

**Interfaces:**
- Consumes: an LLM response object with a `.content: str` attribute.
- Produces: `report_from_response(response) -> EnrichmentReport` that tolerates fenced/` ```json `-wrapped or trailing-prose output from Ollama, raising only when no JSON object can be recovered.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/enrichment/test_reasoning_parse.py
import pytest
from backend.agents.enrichment.reasoning import report_from_response


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content


_VALID = (
    '{"assessment": "malicious", "confidence": 0.91, '
    '"correlation_summary": "C2 beacon matches known infra", '
    '"external_findings": ["intel: malicious"], "internal_findings": [], '
    '"cited_evidence": ["rule 87101"]}'
)


def test_parses_fenced_json_block():
    resp = _Resp("```json\n" + _VALID + "\n```")
    report = report_from_response(resp)
    assert report.assessment == "malicious"
    assert report.confidence == pytest.approx(0.91)


def test_parses_json_with_trailing_prose():
    resp = _Resp(_VALID + "\n\nLet me know if you need more detail.")
    report = report_from_response(resp)
    assert report.assessment == "malicious"


def test_raises_when_no_json_object_present():
    resp = _Resp("I could not analyze this alert.")
    with pytest.raises(Exception):
        report_from_response(resp)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash scripts/run-tests.sh tests/unit/enrichment/test_reasoning_parse.py`
Expected: FAIL — fenced/trailing-prose cases raise `JSONDecodeError`.

- [ ] **Step 3: Implement tolerant extraction**

In `backend/agents/enrichment/reasoning.py`, add a helper and use it inside `report_from_response` before `json.loads`:

```python
import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(content: str) -> str:
    """Recover the first JSON object from possibly-fenced / prose-wrapped LLM text."""
    stripped = content.strip()
    fenced = _FENCE_RE.search(stripped)
    if fenced:
        return fenced.group(1)
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and end > start:
        return stripped[start : end + 1]
    return stripped
```

Then change the parse line in `report_from_response` from `json.loads(content)` to `json.loads(_extract_json_object(content))` (keep the existing `EnrichmentReport.model_validate(...)` call).

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash scripts/run-tests.sh tests/unit/enrichment/test_reasoning_parse.py`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/agents/enrichment/reasoning.py tests/unit/enrichment/test_reasoning_parse.py
git commit -m "fix(enrichment): tolerate fenced/prose-wrapped structured output"
```

### Task C3: Seed demo memory so graph-RAG retrieval returns context

> Apply only if C1 found `search_similar`/`query_fact` returning empty (no seeded episodes/facts for the demo entities).

**Files:**
- Modify: `backend/seed_corpus.py` (extend the existing idempotent one-shot to also write a small demo memory set when memory is enabled)
- Test: `tests/unit/test_seed_memory_facts.py`

**Interfaces:**
- Consumes: the `MemoryStore` protocol (`write_fact`, `write_episode`) and a redactor.
- Produces: `seed_demo_memory(memory, redactor) -> int` returning the number of facts+episodes written; a no-op returning `0` when `memory is None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_seed_memory_facts.py
import asyncio


class _FakeMemory:
    def __init__(self) -> None:
        self.facts: list[tuple] = []
        self.episodes: list[dict] = []

    async def write_fact(self, *, entity, fact_type, value, valid_from=None):
        self.facts.append((entity["value"], fact_type, value))

    async def write_episode(self, episode):
        self.episodes.append(episode)


def test_seed_demo_memory_writes_reputation_facts():
    from backend.seed_corpus import seed_demo_memory

    mem = _FakeMemory()
    count = asyncio.run(seed_demo_memory(mem, redactor=None))
    assert count > 0
    assert any(ft == "reputation" for _, ft, _ in mem.facts)


def test_seed_demo_memory_noop_without_memory():
    from backend.seed_corpus import seed_demo_memory

    assert asyncio.run(seed_demo_memory(None, redactor=None)) == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash scripts/run-tests.sh tests/unit/test_seed_memory_facts.py`
Expected: FAIL — `cannot import name 'seed_demo_memory'`.

- [ ] **Step 3: Implement `seed_demo_memory`**

Add to `backend/seed_corpus.py`:

```python
# Demo IOC reputation facts keyed identically to the verification re-check read-key
# (entity type "indicator"), so enrichment's query_fact(as_of=...) returns context.
_DEMO_IOC_REPUTATION: list[tuple[str, str]] = [
    ("185.220.101.47", "malicious"),
    ("45.137.21.9", "suspicious"),
    ("10.0.1.2", "benign"),
]


async def seed_demo_memory(memory, redactor) -> int:
    """Idempotent best-effort seed of demo reputation facts for graph-RAG retrieval.

    Returns the number of facts written; 0 when memory is disabled/None.
    """
    if memory is None:
        return 0
    written = 0
    for indicator, verdict in _DEMO_IOC_REPUTATION:
        await memory.write_fact(
            entity={"name": indicator, "type": "indicator", "value": indicator},
            fact_type="reputation",
            value=verdict,
        )
        written += 1
    return written
```

Then, in the module's existing async entrypoint (where the corpus is seeded after the container is built), call it best-effort:
```python
    memory = getattr(container, "memory", None)
    try:
        n = await seed_demo_memory(memory, redactor=None)
        logger.info("demo_memory_seeded", facts=n)
    except Exception as exc:  # best-effort — never fail the seed job
        logger.warning("demo_memory_seed_failed", error=str(exc))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash scripts/run-tests.sh tests/unit/test_seed_memory_facts.py`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/seed_corpus.py tests/unit/test_seed_memory_facts.py
git commit -m "feat(memory): seed demo reputation facts for graph-RAG retrieval"
```

### Task C4: Re-run the demo and correct the expected-outcomes table

**Files:**
- Modify: `scripts/demo_full_workflow.sh:420-421` (the `13_enrichment_benign` / `14_enrichment_escalate` expected values)

- [ ] **Step 1: Re-run the enrichment cases after the fix(es)**

Run:
```bash
docker compose run --rm seed-corpus
bash scripts/demo_full_workflow.sh 2>&1 | tee /tmp/demo-run-2.log
docker compose logs worker --since=5m 2>&1 | grep -iE "enrich" | tail -40
```
Expected: enrichment cases now log a real `supervisor_transition ... stage=enrichment outcome=<resolved|advance|escalate>` with non-empty `external_findings`/`internal_findings` in the incident evidence.

- [ ] **Step 2: Update the expected-outcomes map to the observed true outcomes**

Edit `scripts/demo_full_workflow.sh` lines 420-421 to the dispositions actually observed (e.g. `[13_enrichment_benign]="resolved:auto_resolved_enrichment"` and `[14_enrichment_escalate]="escalated:escalated_enrichment"`), removing the `# LLM-dependent` placeholder comments.

- [ ] **Step 3: Verify the demo asserts green for those cases**

Run: `bash scripts/demo_full_workflow.sh 2>&1 | grep -iE "13_enrichment_benign|14_enrichment_escalate"`
Expected: both lines report a PASS/match against the updated expectations.

- [ ] **Step 4: Commit**

```bash
git add scripts/demo_full_workflow.sh
git commit -m "test(demo): correct enrichment cases to true post-fix outcomes"
```

---

## Milestone B — Journey path tags on cards

### Task B1: `JourneyStep` DTO + pure `build_journey` derivation

**Files:**
- Modify: `backend/domain/dashboard.py` (add `JourneyStep`; add `journey` field to `IncidentSummary` and `IncidentDetailView`)
- Modify: `backend/services/pipeline_view.py` (add `build_journey`)
- Test: `tests/unit/test_pipeline_view_journey.py`

**Interfaces:**
- Produces: `JourneyStep(stage: str, label: str, outcome: str, detail: str | None, score: float | None)` and `build_journey(incident) -> list[JourneyStep]` (pure; reads `incident.source`, `incident.evidence`, `incident.status.value`, `incident.disposition`).
- Consumed by: B2 (queue + detail wiring), B3 (frontend rendering).

- [ ] **Step 1: Add the DTO**

In `backend/domain/dashboard.py`, after `IncidentSummary` (before `QueuePage`):
```python
class JourneyStep(BaseModel):
    """One stop on an incident's path through the pipeline (read-only projection)."""

    stage: str  # "intake" | "triage" | "enrichment" | "response" | "terminal"
    label: str
    outcome: str  # "advance" | "resolved" | "escalated" | "errored"
    detail: str | None = None
    score: float | None = None
```
Add `journey: list[JourneyStep] = Field(default_factory=list)` to **both** `IncidentSummary` and `IncidentDetailView`.

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_pipeline_view_journey.py
import uuid
from datetime import UTC, datetime

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.services.pipeline_view import build_journey


def _incident(*, status, disposition=None, source="wazuh", evidence=None):
    now = datetime.now(UTC)
    return Incident(
        id=uuid.uuid4(),
        status=status,
        severity=Severity.HIGH,
        correlation_id="c",
        dedup_fingerprint="f",
        source=source,
        raw_alert={},
        normalized_event={},
        evidence=evidence or {},
        disposition=disposition,
        attempts=0,
        created_at=now,
        updated_at=now,
    )


def test_noise_autoclosed_at_intake():
    inc = _incident(status=IncidentStatus.RESOLVED, disposition="auto_resolved_noise")
    steps = build_journey(inc)
    assert [s.stage for s in steps] == ["intake", "terminal"]
    assert steps[-1].outcome == "resolved"


def test_full_path_resolved():
    ev = {
        "triage": {"verdict": "real", "confidence": 0.82},
        "enrichment": {"assessment": "malicious", "confidence": 0.91},
        "response": {"plan": {"playbook_id": "isolate_and_ticket"},
                     "verification": {"verdict": "verified"}},
    }
    inc = _incident(status=IncidentStatus.RESOLVED, disposition="remediated", evidence=ev)
    stages = [s.stage for s in build_journey(inc)]
    assert stages == ["intake", "triage", "enrichment", "response", "terminal"]
    triage = next(s for s in build_journey(inc) if s.stage == "triage")
    assert triage.outcome == "advance"
    assert triage.score == 0.82


def test_triage_escalation():
    ev = {"triage": {"verdict": "uncertain", "confidence": 0.4}}
    inc = _incident(status=IncidentStatus.ESCALATED, disposition="escalated_triage", evidence=ev)
    triage = next(s for s in build_journey(inc) if s.stage == "triage")
    assert triage.outcome == "escalated"


def test_safety_net_error_marks_terminal_errored():
    inc = _incident(status=IncidentStatus.ESCALATED, disposition="escalated_stage_error")
    steps = build_journey(inc)
    assert steps[-1].stage == "terminal"
    assert steps[-1].outcome == "errored"


def test_intake_source_label_for_anomaly():
    inc = _incident(status=IncidentStatus.TRIAGING, source="anomaly-detector",
                    evidence={"triage": {"verdict": "real", "confidence": 0.7}})
    assert build_journey(inc)[0].detail == "anomaly-detector"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `bash scripts/run-tests.sh tests/unit/test_pipeline_view_journey.py`
Expected: FAIL — `cannot import name 'build_journey'`.

- [ ] **Step 4: Implement `build_journey`**

Add to `backend/services/pipeline_view.py` (it already imports `_DISPOSITION_TO_BRANCH`, `_DISPOSITION_TO_TERMINAL_BRANCH`, `STAGES`; import `JourneyStep` from `backend.domain.dashboard`):

```python
_ERRORED_DISPOSITIONS = frozenset(
    {"escalated_stage_error", "escalated_step_cap", "escalated_token_cap",
     "escalated_illegal_transition"}
)

_STAGE_ORDER = ["triage", "enrichment", "response"]


def _conf(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.0 else None


def build_journey(incident) -> list[JourneyStep]:
    """Derive an incident's ordered stage path from evidence + status + disposition.

    Pure: only reads the incident projection. Stages that were never reached are
    omitted; a reached stage with no downstream progress and a safety-net escalation
    is marked 'errored'.
    """
    evidence = incident.evidence or {}
    status = incident.status.value
    disposition = incident.disposition
    triage = evidence.get("triage") or {}
    enrichment = evidence.get("enrichment") or {}
    response = evidence.get("response") or {}

    steps: list[JourneyStep] = [
        JourneyStep(stage="intake", label="Intake", outcome="advance", detail=incident.source)
    ]
    if disposition == "auto_resolved_noise":
        steps[0].outcome = "resolved"

    present = {
        "triage": bool(triage),
        "enrichment": bool(enrichment),
        "response": bool(response),
    }
    current_stage = _STATUS_TO_STAGE.get(status)

    for i, stage in enumerate(_STAGE_ORDER):
        reached = present[stage] or current_stage == stage
        if not reached:
            continue
        downstream_reached = any(
            present[s] for s in _STAGE_ORDER[i + 1 :]
        ) or (current_stage in _STAGE_ORDER[i + 1 :])

        if stage == "triage":
            detail = triage.get("verdict")
            score = _conf(triage.get("confidence"))
        elif stage == "enrichment":
            detail = enrichment.get("assessment")
            score = _conf(enrichment.get("confidence"))
        else:  # response
            plan = response.get("plan") or {}
            verification = response.get("verification") or {}
            detail = plan.get("playbook_id") or verification.get("verdict")
            score = None

        if downstream_reached:
            outcome = "advance"
        else:
            mapped = _DISPOSITION_TO_BRANCH.get(disposition or "")
            if mapped and mapped[0] == stage:
                outcome = mapped[1]
            elif current_stage == stage:
                outcome = "advance"  # in-flight, no terminal yet
            else:
                outcome = "advance"
        steps.append(
            JourneyStep(stage=stage, label=stage.capitalize(), outcome=outcome,
                        detail=detail, score=score)
        )

    if status in ("resolved", "escalated", "failed"):
        if disposition in _ERRORED_DISPOSITIONS:
            term_outcome = "errored"
        else:
            term_outcome = _DISPOSITION_TO_TERMINAL_BRANCH.get(disposition or "", "escalated")
        steps.append(
            JourneyStep(stage="terminal", label=disposition or status,
                        outcome=term_outcome, detail=disposition)
        )
    return steps
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `bash scripts/run-tests.sh tests/unit/test_pipeline_view_journey.py`
Expected: PASS (5 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/domain/dashboard.py backend/services/pipeline_view.py tests/unit/test_pipeline_view_journey.py
git commit -m "feat(dashboard): derive per-incident journey path (pure)"
```

### Task B2: Attach `journey` to queue rows and incident detail

**Files:**
- Modify: `backend/repositories/incidents.py` (`list_for_queue` — select `evidence`, `disposition` and build journey per row)
- Modify: `backend/routers/incidents.py` (`get_incident` — set `journey=build_journey(incident)`)
- Test: `tests/unit/test_incidents_router_journey.py` (detail path, pure-ish via fakes) and extend an existing repo test if present

**Interfaces:**
- Consumes: `build_journey` from B1.
- Produces: `IncidentSummary.journey` populated on every queue row; `IncidentDetailView.journey` populated on detail.

- [ ] **Step 1: Write the failing test (detail endpoint sets journey)**

```python
# tests/unit/test_incidents_router_journey.py
import uuid
from datetime import UTC, datetime

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.routers import incidents as incidents_router


class _FakeIncidentRepo:
    def __init__(self, incident):
        self._incident = incident
    async def get(self, _id):
        return self._incident


class _FakeAuditRepo:
    async def list_for_incident(self, _id):
        return []


class _FakeApprovalRepo:
    async def get_pending_for_incident(self, _id):
        return None


class _NoopRedactor:
    def redact_mapping(self, mapping, _boundary):
        return mapping


@pytest.mark.asyncio
async def test_get_incident_includes_journey():
    now = datetime.now(UTC)
    inc = Incident(
        id=uuid.uuid4(), status=IncidentStatus.ESCALATED, severity=Severity.HIGH,
        correlation_id="c", dedup_fingerprint="f", source="wazuh", raw_alert={},
        normalized_event={}, evidence={"triage": {"verdict": "uncertain", "confidence": 0.4}},
        disposition="escalated_triage", attempts=0, created_at=now, updated_at=now,
    )
    view = await incidents_router.get_incident(
        inc.id, _FakeIncidentRepo(inc), _FakeAuditRepo(), _FakeApprovalRepo(), _NoopRedactor()
    )
    assert [s.stage for s in view.journey] == ["intake", "triage", "terminal"]
    assert view.journey[-1].outcome == "escalated"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash scripts/run-tests.sh tests/unit/test_incidents_router_journey.py`
Expected: FAIL — `IncidentDetailView` has no populated `journey` (empty list ≠ expected stages).

- [ ] **Step 3: Wire detail + queue**

In `backend/routers/incidents.py`, import and set the field:
```python
from backend.services.pipeline_view import build_journey, build_pipeline_snapshot
```
In the `IncidentDetailView(...)` return, add: `journey=build_journey(incident),`.

In `backend/repositories/incidents.py` `list_for_queue`, change the SELECT to also fetch `evidence` and reconstruct enough of an incident to derive the journey. Replace the summary build with:
```python
from backend.services.pipeline_view import build_journey
...
sql = (
    "SELECT id, status, severity, disposition, source, evidence, "
    "evidence->>'summary' AS summary, updated_at, created_at "
    f"FROM incidents{where} ORDER BY {order} LIMIT :limit OFFSET :offset"
)
result = await self._session.execute(sa.text(sql), params)
rows = result.mappings().all()
summaries: list[IncidentSummary] = []
for row in rows:
    inc = _row_journey_stub(row)
    summaries.append(
        IncidentSummary(
            id=row["id"], status=row["status"], severity=row["severity"],
            disposition=row["disposition"], source=row["source"], summary=row["summary"],
            is_awaiting_approval=row["status"] == "awaiting_approval",
            created_at=row["created_at"], updated_at=row["updated_at"],
            journey=build_journey(inc),
        )
    )
return summaries
```
Add a small module-level stub builder near `_row_to_incident`:
```python
class _JourneyStub:
    """Minimal duck-typed incident slice for build_journey (status/source/evidence/disposition)."""
    def __init__(self, *, status, source, evidence, disposition):
        self.status = IncidentStatus(status)
        self.source = source
        self.evidence = evidence
        self.disposition = disposition


def _row_journey_stub(row: Any) -> _JourneyStub:
    return _JourneyStub(
        status=row["status"], source=row["source"],
        evidence=row["evidence"] or {}, disposition=row["disposition"],
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash scripts/run-tests.sh tests/unit/test_incidents_router_journey.py`
Expected: PASS.

- [ ] **Step 5: Run the existing dashboard repo/router tests to check for regressions**

Run: `bash scripts/run-tests.sh tests/unit/test_pipeline_view_journey.py tests/unit/test_incidents_router_journey.py`
Expected: PASS. Also run any existing queue test file if present (search `tests` for `list_for_queue`).

- [ ] **Step 6: Commit**

```bash
git add backend/repositories/incidents.py backend/routers/incidents.py tests/unit/test_incidents_router_journey.py
git commit -m "feat(dashboard): attach journey to queue rows and incident detail"
```

### Task B3: Frontend `JourneyTrace` component + render on cards

**Files:**
- Create: `frontend/src/components/JourneyTrace.tsx`
- Modify: `frontend/src/api/incidents.ts` (add `JourneyStep` type + `journey` on `IncidentSummary`/`IncidentDetailView`)
- Modify: `frontend/src/features/map/HumanAttentionLane.tsx` (render trace in `EscalatedCard`)
- Modify: `frontend/src/features/map/IncidentDrawer.tsx` (render trace in the drawer header)
- Test: `frontend/src/components/JourneyTrace.test.tsx`

**Interfaces:**
- Consumes: `IncidentSummary.journey: JourneyStep[]` from B2.
- Produces: `<JourneyTrace steps={...} />` rendering a compact color-coded chip row.

- [ ] **Step 1: Add types**

In `frontend/src/api/incidents.ts`, add and wire:
```ts
export interface JourneyStep {
  stage: string
  label: string
  outcome: 'advance' | 'resolved' | 'escalated' | 'errored'
  detail: string | null
  score: number | null
}
```
Add `journey: JourneyStep[]` to both `IncidentSummary` and `IncidentDetailView`.

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/components/JourneyTrace.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { JourneyTrace } from './JourneyTrace'

describe('JourneyTrace', () => {
  it('renders a chip per step with label and score', () => {
    render(
      <JourneyTrace
        steps={[
          { stage: 'intake', label: 'Intake', outcome: 'advance', detail: 'wazuh', score: null },
          { stage: 'triage', label: 'Triage', outcome: 'advance', detail: 'real', score: 0.82 },
          { stage: 'terminal', label: 'remediated', outcome: 'resolved', detail: 'remediated', score: null },
        ]}
      />
    )
    expect(screen.getByText('Intake')).toBeInTheDocument()
    expect(screen.getByText(/0\.82/)).toBeInTheDocument()
    expect(screen.getByTestId('journey-step-terminal')).toHaveTextContent('remediated')
  })

  it('renders nothing for an empty path', () => {
    const { container } = render(<JourneyTrace steps={[]} />)
    expect(container.firstChild).toBeNull()
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend && npm run test -- src/components/JourneyTrace.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the component**

```tsx
// frontend/src/components/JourneyTrace.tsx
import type { JourneyStep } from '@/api/incidents'

const OUTCOME_CLASS: Record<string, string> = {
  advance: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/30',
  resolved: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30',
  escalated: 'bg-amber-500/10 text-amber-300 border-amber-500/30',
  errored: 'bg-red-500/10 text-red-300 border-red-500/30',
}

export function JourneyTrace({ steps }: { steps: JourneyStep[] }) {
  if (!steps || steps.length === 0) return null
  return (
    <div className="flex items-center gap-1 flex-wrap" data-testid="journey-trace">
      {steps.map((step, i) => (
        <div key={`${step.stage}-${i}`} className="flex items-center gap-1">
          <span
            data-testid={`journey-step-${step.stage}`}
            className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[10px] font-medium ${
              OUTCOME_CLASS[step.outcome] ?? 'bg-slate-700/40 text-slate-300 border-slate-600/40'
            }`}
            title={step.detail ?? undefined}
          >
            {step.label}
            {step.detail && step.stage !== 'terminal' && (
              <span className="opacity-70">· {step.detail}</span>
            )}
            {typeof step.score === 'number' && (
              <span className="font-mono opacity-80">{step.score.toFixed(2)}</span>
            )}
          </span>
          {i < steps.length - 1 && <span className="text-slate-600 text-[10px]">→</span>}
        </div>
      ))}
    </div>
  )
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend && npm run test -- src/components/JourneyTrace.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 6: Render the trace on the escalated card and drawer**

In `frontend/src/features/map/HumanAttentionLane.tsx`, import `import { JourneyTrace } from '@/components/JourneyTrace'` and inside `EscalatedCard`'s `CardContent`, after the summary line, add:
```tsx
<JourneyTrace steps={incident.journey} />
```
In `frontend/src/features/map/IncidentDrawer.tsx`, render `<JourneyTrace steps={detail.journey} />` in the header region (use the detail query's data; guard with `detail?.journey ?? []`).

- [ ] **Step 7: Typecheck + build**

Run: `cd frontend && npm run build`
Expected: type-checks and builds with no errors.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/JourneyTrace.tsx frontend/src/components/JourneyTrace.test.tsx frontend/src/api/incidents.ts frontend/src/features/map/HumanAttentionLane.tsx frontend/src/features/map/IncidentDrawer.tsx
git commit -m "feat(ui): show per-incident journey trace on cards and drawer"
```

---

## Milestone A — Acknowledge + Resolve on escalated incidents

### Task A1: Migration 0007 — acknowledge columns

**Files:**
- Create: `backend/db/migrations/versions/0007_incident_acknowledge.py`

- [ ] **Step 1: Write the migration**

```python
"""Add acknowledged_at / acknowledged_by to incidents (operator acknowledge action).

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-18

Additive only — nullable columns. Status/disposition unchanged.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "incidents",
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column("incidents", sa.Column("acknowledged_by", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "acknowledged_by")
    op.drop_column("incidents", "acknowledged_at")
```

- [ ] **Step 2: Apply and verify**

Run:
```bash
docker compose run --rm migrate
docker compose exec postgres psql -U argus -d argus -c "\d incidents" | grep acknowledged
```
Expected: both `acknowledged_at` and `acknowledged_by` columns listed.

- [ ] **Step 3: Commit**

```bash
git add backend/db/migrations/versions/0007_incident_acknowledge.py
git commit -m "feat(db): add incident acknowledge columns (0007)"
```

### Task A2: Domain fields + repository `acknowledge`

**Files:**
- Modify: `backend/domain/incident.py` (add `acknowledged_at` / `acknowledged_by` to `Incident`)
- Modify: `backend/repositories/incidents.py` (`acknowledge`, and read the columns in `_row_to_incident`)
- Modify: `backend/domain/dashboard.py` (`IncidentSummary` gets `acknowledged_at: datetime | None = None`)
- Test: `tests/integration/test_incident_acknowledge_repo.py`

**Interfaces:**
- Produces: `IncidentRepository.acknowledge(incident_id, *, actor) -> bool` — guarded `UPDATE … WHERE status='escalated' AND acknowledged_at IS NULL`, returns True iff it set the columns.

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_incident_acknowledge_repo.py
import uuid
import pytest

from backend.domain.incident import IncidentStatus
from backend.repositories.incidents import IncidentRepository

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_acknowledge_sets_columns_only_when_escalated(db_session, make_incident):
    repo = IncidentRepository(db_session)
    inc = await make_incident(status=IncidentStatus.ESCALATED, disposition="escalated_triage")

    ok = await repo.acknowledge(inc.id, actor="alice")
    assert ok is True

    again = await repo.acknowledge(inc.id, actor="bob")
    assert again is False  # already acknowledged

    reloaded = await repo.get(inc.id)
    assert reloaded.acknowledged_by == "alice"
    assert reloaded.acknowledged_at is not None
```
(If `make_incident`/`db_session` fixtures differ, mirror the helpers used by the existing integration tests under `tests/integration/`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash scripts/run-tests.sh tests/integration/test_incident_acknowledge_repo.py`
Expected: FAIL — `acknowledge` not defined.

- [ ] **Step 3: Implement domain fields + repo method + row read**

In `backend/domain/incident.py`, add to `Incident`:
```python
    acknowledged_at: datetime | None = None
    acknowledged_by: str | None = None
```
In `backend/repositories/incidents.py`, add the method (near `advance_status`):
```python
    async def acknowledge(self, incident_id: uuid.UUID, *, actor: str) -> bool:
        """Mark an escalated incident acknowledged (idempotent; status unchanged)."""
        now = datetime.now(UTC)
        result = await self._session.execute(
            sa.text(
                "UPDATE incidents SET acknowledged_at = :now, acknowledged_by = :actor, "
                "updated_at = :now "
                "WHERE id = :id AND status = 'escalated' AND acknowledged_at IS NULL "
                "RETURNING id"
            ),
            {"id": str(incident_id), "actor": actor, "now": now},
        )
        await self._session.commit()
        return result.first() is not None
```
Extend `_row_to_incident` to read the new columns (using `.get` for compatibility with selects that omit them):
```python
        acknowledged_at=row.get("acknowledged_at"),
        acknowledged_by=row.get("acknowledged_by"),
```
And ensure `get`'s `SELECT` includes `acknowledged_at, acknowledged_by` (it selects `*` or an explicit list — add them if explicit).

In `backend/domain/dashboard.py`, add to `IncidentSummary`: `acknowledged_at: datetime | None = None`. In `list_for_queue`, add `acknowledged_at` to the SELECT and pass `acknowledged_at=row["acknowledged_at"]` into `IncidentSummary(...)`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash scripts/run-tests.sh tests/integration/test_incident_acknowledge_repo.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/domain/incident.py backend/repositories/incidents.py backend/domain/dashboard.py tests/integration/test_incident_acknowledge_repo.py
git commit -m "feat(incidents): repository acknowledge + domain/DTO fields"
```

### Task A3: `Supervisor.close_incident` + disposition registration

**Files:**
- Modify: `backend/services/supervisor.py` (add `DISP_OPERATOR_RESOLVED` + `close_incident`)
- Modify: `backend/services/pipeline_view.py` (`_DISPOSITION_TO_TERMINAL_BRANCH` += `operator_resolved → resolved`)
- Test: `tests/unit/test_supervisor_close.py`

**Interfaces:**
- Produces: `Supervisor.close_incident(incident_id, repo, audit_repo=None, actor="operator") -> bool` — guarded `ESCALATED → RESOLVED` with disposition `operator_resolved`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_supervisor_close.py
import uuid
import pytest

from backend.domain.incident import IncidentStatus
from backend.services.supervisor import DISP_OPERATOR_RESOLVED, Supervisor
from backend.infra.config import SupervisorSettings
from backend.infra.tracing import _Tracer


class _Repo:
    def __init__(self):
        self.calls = []
    async def advance_status(self, incident_id, *, expected, target, disposition=None, evidence_patch=None):
        self.calls.append((expected, target, disposition))
        return expected == IncidentStatus.ESCALATED


class _Audit:
    def __init__(self):
        self.rows = []
    async def append(self, **kw):
        self.rows.append(kw)


def _sup():
    return Supervisor(stages={}, cfg=SupervisorSettings(), tracer=_Tracer(enabled=False))


@pytest.mark.asyncio
async def test_close_incident_escalated_to_resolved():
    sup = _sup()
    repo, audit = _Repo(), _Audit()
    ok = await sup.close_incident(uuid.uuid4(), repo, audit_repo=audit, actor="alice")
    assert ok is True
    assert repo.calls[0] == (IncidentStatus.ESCALATED, IncidentStatus.RESOLVED, DISP_OPERATOR_RESOLVED)
    assert audit.rows and audit.rows[0]["action"] == "operator_resolved"
```
(If `_Tracer(enabled=False)` is not the real constructor, mirror how other supervisor unit tests build the tracer.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash scripts/run-tests.sh tests/unit/test_supervisor_close.py`
Expected: FAIL — `cannot import name 'DISP_OPERATOR_RESOLVED'`.

- [ ] **Step 3: Implement**

In `backend/services/supervisor.py`, add near the other `DISP_*` constants:
```python
DISP_OPERATOR_RESOLVED = "operator_resolved"
```
Add the method to `Supervisor` (mirrors `expire_incident`):
```python
    async def close_incident(
        self,
        incident_id: uuid.UUID,
        repo: object,
        audit_repo: object | None = None,
        actor: str = "operator",
    ) -> bool:
        """Operator-driven manual close of an escalated incident (single-writer edge).

        ESCALATED → RESOLVED (operator_resolved). Returns True iff the guard held.
        Does NOT emit a remediation_outcome feedback fact (no remediation occurred).
        """
        advanced = await repo.advance_status(
            incident_id,
            expected=IncidentStatus.ESCALATED,
            target=IncidentStatus.RESOLVED,
            disposition=DISP_OPERATOR_RESOLVED,
        )
        if not advanced:
            logger.info("supervisor_close_guard_lost", incident_id=str(incident_id))
            return False
        logger.info("supervisor_incident_closed", incident_id=str(incident_id), actor=actor)
        if audit_repo is not None:
            try:
                await audit_repo.append(
                    incident_id=incident_id, actor=actor, action="operator_resolved",
                    target=None, outcome="resolved",
                )
            except Exception:
                pass
        return True
```
In `backend/services/pipeline_view.py`, add `"operator_resolved": "resolved"` to `_DISPOSITION_TO_TERMINAL_BRANCH`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash scripts/run-tests.sh tests/unit/test_supervisor_close.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/services/supervisor.py backend/services/pipeline_view.py tests/unit/test_supervisor_close.py
git commit -m "feat(supervisor): operator close_incident (escalated -> resolved)"
```

### Task A4: Endpoints — POST acknowledge + resolve

**Files:**
- Modify: `backend/routers/incidents.py` (add the two POST routes + needed deps)
- Test: `tests/unit/test_incidents_actions_router.py`

**Interfaces:**
- Produces: `POST /incidents/{id}/acknowledge` and `POST /incidents/{id}/resolve`, each returning `{incident_id, status, disposition?}`; 404 if missing, 409 if not escalated.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_incidents_actions_router.py
import uuid
from datetime import UTC, datetime
import pytest
from fastapi import HTTPException

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.dashboard import OperatorSession
from backend.routers import incidents as r


def _inc(status):
    now = datetime.now(UTC)
    return Incident(id=uuid.uuid4(), status=status, severity=Severity.HIGH, correlation_id="c",
                    dedup_fingerprint="f", source="wazuh", raw_alert={}, normalized_event={},
                    evidence={}, disposition="escalated_triage", attempts=0,
                    created_at=now, updated_at=now)


class _IncRepo:
    def __init__(self, inc): self._inc = inc; self.ack = False
    async def get(self, _id): return self._inc
    async def acknowledge(self, _id, *, actor): self.ack = True; return True


class _Audit:
    async def append(self, **kw): return None


class _Sup:
    async def close_incident(self, _id, _repo, audit_repo=None, actor="operator"): return True


_OP = OperatorSession(subject="alice", role="admin", expires_at=datetime.now(UTC))


@pytest.mark.asyncio
async def test_acknowledge_ok():
    inc = _inc(IncidentStatus.ESCALATED)
    repo = _IncRepo(inc)
    out = await r.acknowledge_incident(inc.id, repo, _Audit(), _OP)
    assert repo.ack is True and out["status"] == "escalated"


@pytest.mark.asyncio
async def test_resolve_rejects_non_escalated():
    inc = _inc(IncidentStatus.TRIAGING)
    with pytest.raises(HTTPException) as e:
        await r.resolve_incident(inc.id, _IncRepo(inc), _Audit(), _Sup(), _OP)
    assert e.value.status_code == 409
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `bash scripts/run-tests.sh tests/unit/test_incidents_actions_router.py`
Expected: FAIL — `acknowledge_incident` / `resolve_incident` not defined.

- [ ] **Step 3: Implement the endpoints**

In `backend/routers/incidents.py`, extend the dependency imports:
```python
from backend.dependencies import (
    get_approval_repo, get_audit_repo, get_current_operator, get_incident_repo,
    get_redactor_dep, get_supervisor, get_trace_repo,
)
```
Add the routes (after `get_audit`):
```python
@router.post("/{incident_id}/acknowledge")
async def acknowledge_incident(
    incident_id: uuid.UUID,
    incident_repo=Depends(get_incident_repo),
    audit_repo=Depends(get_audit_repo),
    operator=Depends(get_current_operator),
) -> dict:
    incident = await incident_repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status.value != "escalated":
        raise HTTPException(status_code=409, detail="Only escalated incidents can be acknowledged")
    await incident_repo.acknowledge(incident_id, actor=operator.subject)
    try:
        await audit_repo.append(incident_id=incident_id, actor=operator.subject,
                                action="acknowledged", target=None, outcome="acknowledged")
    except Exception:
        pass
    return {"incident_id": str(incident_id), "status": "escalated", "acknowledged_by": operator.subject}


@router.post("/{incident_id}/resolve")
async def resolve_incident(
    incident_id: uuid.UUID,
    incident_repo=Depends(get_incident_repo),
    audit_repo=Depends(get_audit_repo),
    supervisor=Depends(get_supervisor),
    operator=Depends(get_current_operator),
) -> dict:
    incident = await incident_repo.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.status.value != "escalated":
        raise HTTPException(status_code=409, detail="Only escalated incidents can be resolved")
    ok = await supervisor.close_incident(incident_id, incident_repo,
                                         audit_repo=audit_repo, actor=operator.subject)
    if not ok:
        raise HTTPException(status_code=409, detail="Incident was no longer escalated")
    return {"incident_id": str(incident_id), "status": "resolved", "disposition": "operator_resolved"}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `bash scripts/run-tests.sh tests/unit/test_incidents_actions_router.py`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/routers/incidents.py tests/unit/test_incidents_actions_router.py
git commit -m "feat(api): POST /incidents/{id}/acknowledge and /resolve"
```

### Task A5: Frontend — acknowledge/resolve hooks + buttons + filter

**Files:**
- Modify: `frontend/src/api/incidents.ts` (add `useAcknowledgeIncident`, `useResolveIncident`; `acknowledged_at` on `IncidentSummary`)
- Create: `frontend/src/components/ConfirmDialog.tsx` (generic confirm; mirrors `DecisionDialog`)
- Modify: `frontend/src/features/map/HumanAttentionLane.tsx` (buttons on `EscalatedCard`; filter out acknowledged)
- Test: `frontend/src/features/map/HumanAttentionLane.test.tsx` (filter behavior)

**Interfaces:**
- Consumes: the two POST endpoints from A4.
- Produces: acknowledge/resolve mutations that invalidate `['incidents','queue']`.

- [ ] **Step 1: Add API hooks + type**

In `frontend/src/api/incidents.ts`, add `acknowledged_at: string | null` to `IncidentSummary`, then:
```ts
import { useMutation, useQueryClient } from '@tanstack/react-query'

export function useAcknowledgeIncident() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => apiFetch(`/incidents/${id}/acknowledge`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['incidents', 'queue'] }),
  })
}

export function useResolveIncident() {
  const qc = useQueryClient()
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => apiFetch(`/incidents/${id}/resolve`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['incidents', 'queue'] }),
  })
}
```

- [ ] **Step 2: Create the generic confirm dialog**

```tsx
// frontend/src/components/ConfirmDialog.tsx
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'

interface ConfirmDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description: string
  confirmLabel: string
  onConfirm: () => void
  isLoading: boolean
}

export function ConfirmDialog({ open, onOpenChange, title, description, confirmLabel, onConfirm, isLoading }: ConfirmDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        <p className="text-sm text-slate-300">{description}</p>
        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={isLoading}>Cancel</Button>
          <Button size="sm" onClick={onConfirm} disabled={isLoading}>{confirmLabel}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
```
(If `ui/dialog` does not export `DialogFooter`, use the named exports it does provide — check `frontend/src/components/ui/dialog.tsx`.)

- [ ] **Step 3: Write the failing filter test**

```tsx
// frontend/src/features/map/HumanAttentionLane.test.tsx
import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'

vi.mock('@/api/approvals', () => ({ usePendingApprovals: () => ({ data: { approvals: [] } }), useApprovalDecision: () => ({ mutate: vi.fn(), isPending: false }) }))
vi.mock('@/api/incidents', () => ({
  useIncidentQueue: () => ({ data: { items: [
    { id: 'a', status: 'escalated', severity: 'high', disposition: 'escalated_triage', source: 'wazuh', summary: 's', is_awaiting_approval: false, created_at: '', updated_at: '', acknowledged_at: null, journey: [] },
    { id: 'b', status: 'escalated', severity: 'high', disposition: 'escalated_triage', source: 'wazuh', summary: 's', is_awaiting_approval: false, created_at: '', updated_at: '', acknowledged_at: '2026-06-18T00:00:00Z', journey: [] },
  ] } }),
  useAcknowledgeIncident: () => ({ mutate: vi.fn(), isPending: false }),
  useResolveIncident: () => ({ mutate: vi.fn(), isPending: false }),
}))

import { HumanAttentionLane } from './HumanAttentionLane'

describe('HumanAttentionLane', () => {
  it('hides acknowledged escalated incidents', () => {
    render(<HumanAttentionLane onSelectIncident={() => {}} />)
    expect(screen.getByTestId('escalated-card-a')).toBeInTheDocument()
    expect(screen.queryByTestId('escalated-card-b')).toBeNull()
  })
})
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd frontend && npm run test -- src/features/map/HumanAttentionLane.test.tsx`
Expected: FAIL — acknowledged card `b` still rendered (no filter yet).

- [ ] **Step 5: Wire buttons + filter in `HumanAttentionLane.tsx`**

Add imports for `useAcknowledgeIncident`, `useResolveIncident`, `ConfirmDialog`. In `EscalatedCard`, add Acknowledge + Resolve buttons using a local `pendingAction` state and `ConfirmDialog` (mirror `AwaitingCard`'s dialog pattern), calling the mutations with `incident.id`. In `HumanAttentionLane`, filter: `const escalated = (escalatedPage?.items ?? []).filter((i) => !i.acknowledged_at)`.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npm run test -- src/features/map/HumanAttentionLane.test.tsx`
Expected: PASS.

- [ ] **Step 7: Typecheck + build**

Run: `cd frontend && npm run build`
Expected: builds clean.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/incidents.ts frontend/src/components/ConfirmDialog.tsx frontend/src/features/map/HumanAttentionLane.tsx frontend/src/features/map/HumanAttentionLane.test.tsx
git commit -m "feat(ui): acknowledge + resolve actions on escalated cards"
```

---

## Final verification

- [ ] **Backend suite (touched areas):**
Run: `bash scripts/run-tests.sh tests/unit/test_pipeline_view_journey.py tests/unit/test_incidents_router_journey.py tests/unit/test_supervisor_close.py tests/unit/test_incidents_actions_router.py tests/integration/test_incident_acknowledge_repo.py`
Expected: all PASS.

- [ ] **Frontend suite + build:**
Run: `cd frontend && npm run test && npm run build`
Expected: all PASS, clean build.

- [ ] **End-to-end demo:**
Run: `docker compose run --rm migrate && docker compose run --rm seed-corpus && bash scripts/demo_full_workflow.sh`
Expected: enrichment cases flow through enrichment with retrieved context; escalated incidents show a journey trace and can be acknowledged/resolved from Human Attention.

---

## Self-review notes (spec coverage)

- Spec Thread C (diagnose-then-fix, exit criteria, best-effort retrieval invariant) → Tasks C1–C4.
- Spec Thread B (backend-derived `journey` on `IncidentSummary`+`IncidentDetailView`, source-aware intake, color-coded chips on queue/attention/drawer) → Tasks B1–B3.
- Spec Thread A (additive 0007 columns, acknowledge keeps `ESCALATED`, `close_incident` single-writer edge with `operator_resolved` kept out of the feedback loop, both endpoints behind admin auth, audit rows) → Tasks A1–A5.
- Cross-cutting (auth reuse, audit rows, run-tests.sh, no new eval gate, disposition registered in `pipeline_view`) → covered in A3/A4 and Global Constraints.
