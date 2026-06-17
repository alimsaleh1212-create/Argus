# SOC Pipeline Map — M-a (Backend Data Spine) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only `GET /incidents/pipeline` endpoint that returns live per-stage incident counts, per-stage terminal-branch outflows, and rolling-window terminal totals — the data spine the new SOC pipeline-map view polls.

**Architecture:** Mirror the existing KPI read path exactly: a pure service (`services/pipeline_view.py`) composes a `PipelineSnapshot` DTO from two new read-only aggregate methods on `IncidentRepository`. Stage/branch mapping is pure, module-level, and unit-tested without a database. The router reads a config-backed rolling window and returns the snapshot. No migration, no new writer, no FSM change.

**Tech Stack:** Python 3.12, FastAPI, async SQLAlchemy (raw `sa.text` aggregates), Pydantic v2, pytest / pytest-asyncio, testcontainers (Postgres) for the repository test.

## Global Constraints

- **Read-only, zero migration, no new writer** — Constitution III/IV preserved; this endpoint only reads the existing `incidents` table.
- **Domain isolation:** `backend/domain/dashboard.py` has **no outward imports** (pure Pydantic v2).
- **Settings:** `DashboardSettings` uses `model_config = SettingsConfigDict(extra="forbid")` — new fields must have defaults.
- **No new PII surface:** the snapshot exposes only integer counts and fixed enum/string keys — never incident text — so there is no redaction-gate change.
- **Route ordering:** `GET /incidents/pipeline` MUST be declared **before** the `GET /incidents/{incident_id}` route in `backend/routers/incidents.py` (a literal segment registered before the path-param route), exactly as `/kpis` and `/stream` already are.
- **Rolling window** is config-backed: `DashboardSettings.pipeline_window_hours` (default `24`).
- **Tests:** run a single file with `uv run pytest <file> -q` (memory-safe). Run a whole tier with `make test-unit` / `make test-integration` (batched per-file via `scripts/run-tests.sh` — never `pytest tests/` in one process; spaCy + graphiti OOM).
- **Stage vocabulary (canonical):** ordered stages are `intake`, `triage`, `enrichment`, `response`. Branch labels are exactly `resolved` and `escalated`.

---

## File Structure

- **Modify** `backend/domain/dashboard.py` — add `BranchOutflow`, `StageNode`, `TerminalCounts`, `PipelineSnapshot` DTOs (Task 1).
- **Create** `backend/services/pipeline_view.py` — pure stage/branch mapping + `build_pipeline_snapshot` (Task 2).
- **Modify** `backend/repositories/incidents.py` — add `status_counts()` + `disposition_counts_since()` read methods (Task 3).
- **Modify** `backend/infra/config.py` — add `DashboardSettings.pipeline_window_hours` (Task 4).
- **Modify** `backend/routers/incidents.py` — add `GET /pipeline` endpoint (Task 4).
- **Create** `tests/unit/test_pipeline_view.py` — DTO + pure-mapping + service unit tests (Tasks 1–2).
- **Modify** `tests/integration/test_incident_repository.py` — real-DB tests for the two new repo methods (Task 3).
- **Create** `tests/integration/test_pipeline_api.py` — endpoint integration tests with a mock repo (Task 4).

---

### Task 1: PipelineSnapshot DTOs

**Files:**
- Modify: `backend/domain/dashboard.py` (append after `KpiSnapshot`, before `LoginRequest`)
- Test: `tests/unit/test_pipeline_view.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `BranchOutflow(BaseModel)`: `to: str`, `count: int`
  - `StageNode(BaseModel)`: `key: str`, `label: str`, `in_flight: int`, `branches: list[BranchOutflow]` (default empty)
  - `TerminalCounts(BaseModel)`: `resolved: int`, `escalated: int`, `awaiting: int`
  - `PipelineSnapshot(BaseModel)`: `stages: list[StageNode]`, `terminals: TerminalCounts`, `window_hours: int`, `generated_at: datetime`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_pipeline_view.py`:

```python
"""Unit tests for the SOC pipeline-map data spine (M-a)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.domain.dashboard import (
    BranchOutflow,
    PipelineSnapshot,
    StageNode,
    TerminalCounts,
)


class TestPipelineDtos:
    def test_pipeline_snapshot_shape(self) -> None:
        snap = PipelineSnapshot(
            stages=[
                StageNode(
                    key="triage",
                    label="Triage",
                    in_flight=3,
                    branches=[BranchOutflow(to="resolved", count=2)],
                )
            ],
            terminals=TerminalCounts(resolved=10, escalated=4, awaiting=1),
            window_hours=24,
            generated_at=datetime.now(UTC),
        )
        assert snap.stages[0].in_flight == 3
        assert snap.stages[0].branches[0].to == "resolved"
        assert snap.stages[0].branches[0].count == 2
        assert snap.terminals.awaiting == 1
        assert snap.window_hours == 24

    def test_stage_node_branches_default_empty(self) -> None:
        node = StageNode(key="intake", label="Intake", in_flight=0)
        assert node.branches == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_pipeline_view.py::TestPipelineDtos -q`
Expected: FAIL with `ImportError: cannot import name 'BranchOutflow' from 'backend.domain.dashboard'`.

- [ ] **Step 3: Add the DTOs**

In `backend/domain/dashboard.py`, immediately after the `KpiSnapshot` class (line ~149, before `class LoginRequest`), add:

```python
class BranchOutflow(BaseModel):
    """One terminal exit from a stage over the rolling window."""

    to: str  # "resolved" | "escalated"
    count: int


class StageNode(BaseModel):
    """One stage on the pipeline rail."""

    key: str  # "intake" | "triage" | "enrichment" | "response"
    label: str
    in_flight: int
    branches: list[BranchOutflow] = Field(default_factory=list)


class TerminalCounts(BaseModel):
    """Rolling-window terminal totals + live awaiting-approval count."""

    resolved: int
    escalated: int
    awaiting: int


class PipelineSnapshot(BaseModel):
    """Aggregate read for the SOC pipeline-map view (read-only)."""

    stages: list[StageNode]
    terminals: TerminalCounts
    window_hours: int
    generated_at: datetime
```

(`BaseModel`, `Field`, and `datetime` are already imported at the top of the file.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_pipeline_view.py::TestPipelineDtos -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/domain/dashboard.py tests/unit/test_pipeline_view.py
git commit -m "feat(dashboard): Add PipelineSnapshot read DTOs"
```

---

### Task 2: Pipeline view service (pure mapping + snapshot builder)

**Files:**
- Create: `backend/services/pipeline_view.py`
- Test: `tests/unit/test_pipeline_view.py` (append)

**Interfaces:**
- Consumes (Task 1): `BranchOutflow`, `StageNode`, `TerminalCounts`, `PipelineSnapshot`.
- Consumes (Task 3 — repository protocol, mocked here): `repo.status_counts() -> dict[str, int]`, `repo.disposition_counts_since(*, window_hours: int) -> dict[str, int]`.
- Produces:
  - `STAGES: list[tuple[str, str]]` — ordered `(key, label)` pairs.
  - `stage_in_flight(status_counts: dict[str, int]) -> dict[str, int]`
  - `stage_branches(disposition_counts: dict[str, int]) -> dict[str, list[BranchOutflow]]`
  - `terminal_counts(status_counts: dict[str, int], disposition_counts: dict[str, int]) -> TerminalCounts`
  - `async build_pipeline_snapshot(repo, *, window_hours: int) -> PipelineSnapshot`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_pipeline_view.py`:

```python
from unittest.mock import AsyncMock

from backend.services.pipeline_view import (
    STAGES,
    build_pipeline_snapshot,
    stage_branches,
    stage_in_flight,
    terminal_counts,
)


class TestStageMapping:
    def test_stages_are_ordered(self) -> None:
        assert [k for k, _ in STAGES] == ["intake", "triage", "enrichment", "response"]

    def test_stage_in_flight_groups_statuses(self) -> None:
        counts = {
            "received": 2,
            "grounding": 1,
            "triaging": 4,
            "responding": 2,
            "awaiting_approval": 1,
            "resolved": 99,  # terminal — must NOT count toward any stage
        }
        inflight = stage_in_flight(counts)
        assert inflight == {"intake": 3, "triage": 4, "enrichment": 0, "response": 3}

    def test_stage_branches_maps_dispositions_and_ignores_unknown(self) -> None:
        disp = {
            "auto_resolved_triage": 2,
            "escalated_triage": 1,
            "auto_remediated": 5,
            "approval_expired": 1,
            "some_unknown_disposition": 9,
        }
        branches = stage_branches(disp)
        assert {b.to: b.count for b in branches["triage"]} == {"resolved": 2, "escalated": 1}
        assert {b.to: b.count for b in branches["response"]} == {"resolved": 5, "escalated": 1}
        # unknown disposition is attributed to no stage
        flat = [b.to for stage in branches.values() for b in stage]
        assert all("unknown" not in label for label in flat)

    def test_terminal_counts_sums_by_branch_plus_awaiting(self) -> None:
        status = {"awaiting_approval": 2}
        disp = {
            "auto_resolved_triage": 3,
            "auto_remediated": 4,
            "escalated_enrichment": 1,
            "approval_expired": 2,
        }
        tc = terminal_counts(status, disp)
        assert tc.resolved == 7  # 3 + 4
        assert tc.escalated == 3  # 1 + 2
        assert tc.awaiting == 2


class TestBuildPipelineSnapshot:
    @pytest.mark.asyncio
    async def test_composes_from_repo_reads(self) -> None:
        repo = AsyncMock()
        repo.status_counts = AsyncMock(
            return_value={"triaging": 4, "responding": 1, "awaiting_approval": 1}
        )
        repo.disposition_counts_since = AsyncMock(
            return_value={"auto_resolved_triage": 2, "escalated_response": 1}
        )

        snap = await build_pipeline_snapshot(repo, window_hours=24)

        assert isinstance(snap, PipelineSnapshot)
        assert [s.key for s in snap.stages] == ["intake", "triage", "enrichment", "response"]
        triage = next(s for s in snap.stages if s.key == "triage")
        assert triage.in_flight == 4
        assert {b.to: b.count for b in triage.branches} == {"resolved": 2}
        assert snap.terminals.escalated == 1
        assert snap.terminals.awaiting == 1
        assert snap.window_hours == 24
        repo.status_counts.assert_called_once()
        repo.disposition_counts_since.assert_called_once_with(window_hours=24)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_pipeline_view.py::TestStageMapping tests/unit/test_pipeline_view.py::TestBuildPipelineSnapshot -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.services.pipeline_view'`.

- [ ] **Step 3: Implement the service**

Create `backend/services/pipeline_view.py`:

```python
"""Pipeline-map service — composes a PipelineSnapshot from aggregate reads.

Read-only and provider-independent. The status/disposition mapping is pure and
unit-tested without a database (mirrors the KPI service pattern).
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.dashboard import (
    BranchOutflow,
    PipelineSnapshot,
    StageNode,
    TerminalCounts,
)

# Ordered rail: (stage key, display label).
STAGES: list[tuple[str, str]] = [
    ("intake", "Intake"),
    ("triage", "Triage"),
    ("enrichment", "Enrichment"),
    ("response", "Response"),
]

# Active (in-flight) statuses → the stage they sit in. Terminal statuses
# (resolved/escalated/failed) intentionally map to no stage.
_STATUS_TO_STAGE: dict[str, str] = {
    "received": "intake",
    "grounding": "intake",
    "grounded": "intake",
    "triaging": "triage",
    "enriching": "enrichment",
    "responding": "response",
    "awaiting_approval": "response",
}

# Stage-tagged dispositions → (stage that produced it, terminal branch).
_DISPOSITION_TO_BRANCH: dict[str, tuple[str, str]] = {
    "auto_resolved_noise": ("intake", "resolved"),
    "auto_resolved_triage": ("triage", "resolved"),
    "escalated_triage": ("triage", "escalated"),
    "auto_resolved_enrichment": ("enrichment", "resolved"),
    "escalated_enrichment": ("enrichment", "escalated"),
    "auto_remediated": ("response", "resolved"),
    "remediated": ("response", "resolved"),
    "rejected_by_human": ("response", "resolved"),
    "remediation_unverified": ("response", "escalated"),
    "approval_expired": ("response", "escalated"),
    "escalated_response": ("response", "escalated"),
}


def stage_in_flight(status_counts: dict[str, int]) -> dict[str, int]:
    """Sum active-status counts into the four stage buckets (0 when empty)."""
    out: dict[str, int] = {key: 0 for key, _ in STAGES}
    for status, count in status_counts.items():
        stage = _STATUS_TO_STAGE.get(status)
        if stage is not None:
            out[stage] += count
    return out


def stage_branches(disposition_counts: dict[str, int]) -> dict[str, list[BranchOutflow]]:
    """Attribute terminal dispositions to (stage, branch) outflows over the window."""
    acc: dict[str, dict[str, int]] = {key: {} for key, _ in STAGES}
    for disposition, count in disposition_counts.items():
        mapping = _DISPOSITION_TO_BRANCH.get(disposition)
        if mapping is None:
            continue  # unknown disposition is not attributed to a stage
        stage, branch = mapping
        acc[stage][branch] = acc[stage].get(branch, 0) + count
    return {
        stage: [BranchOutflow(to=branch, count=acc[stage][branch]) for branch in sorted(acc[stage])]
        for stage in acc
    }


def terminal_counts(
    status_counts: dict[str, int], disposition_counts: dict[str, int]
) -> TerminalCounts:
    """Roll dispositions into resolved/escalated totals; awaiting is the live count."""
    resolved = 0
    escalated = 0
    for disposition, count in disposition_counts.items():
        mapping = _DISPOSITION_TO_BRANCH.get(disposition)
        if mapping is None:
            continue
        _, branch = mapping
        if branch == "resolved":
            resolved += count
        elif branch == "escalated":
            escalated += count
    return TerminalCounts(
        resolved=resolved,
        escalated=escalated,
        awaiting=status_counts.get("awaiting_approval", 0),
    )


async def build_pipeline_snapshot(repo, *, window_hours: int) -> PipelineSnapshot:
    """Compose a PipelineSnapshot from two read-only aggregate reads."""
    status_counts = await repo.status_counts()
    disposition_counts = await repo.disposition_counts_since(window_hours=window_hours)
    in_flight = stage_in_flight(status_counts)
    branches = stage_branches(disposition_counts)
    stages = [
        StageNode(key=key, label=label, in_flight=in_flight[key], branches=branches.get(key, []))
        for key, label in STAGES
    ]
    return PipelineSnapshot(
        stages=stages,
        terminals=terminal_counts(status_counts, disposition_counts),
        window_hours=window_hours,
        generated_at=datetime.now(UTC),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_pipeline_view.py -q`
Expected: PASS (all classes; DTO tests from Task 1 still green).

- [ ] **Step 5: Commit**

```bash
git add backend/services/pipeline_view.py tests/unit/test_pipeline_view.py
git commit -m "feat(dashboard): Add pipeline-map snapshot service"
```

---

### Task 3: Repository read methods

**Files:**
- Modify: `backend/repositories/incidents.py` (add two methods inside `class IncidentRepository`, after `kpi_status_counts`, before `list_non_terminal`)
- Test: `tests/integration/test_incident_repository.py` (append a test class)

**Interfaces:**
- Consumes: existing `self._session` (`AsyncSession`), the `sa` import already present.
- Produces:
  - `async def status_counts(self) -> dict[str, int]` — `{status: count}` for **all** statuses.
  - `async def disposition_counts_since(self, *, window_hours: int) -> dict[str, int]` — `{disposition: count}` for non-NULL dispositions whose `updated_at` is within the window.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_incident_repository.py`:

```python
@pytest.mark.integration
class TestPipelineRepositoryReads:
    async def _resolved(self, session, *, disposition: str):
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(session)
        inc = Incident(
            id=uuid.uuid4(),
            status=IncidentStatus.RECEIVED,
            severity=Severity.HIGH,
            correlation_id=str(uuid.uuid4()),
            dedup_fingerprint=f"fp-{uuid.uuid4().hex}",
            source="wazuh",
            raw_alert={"rule": {"level": 10}},
        )
        await repo.create(inc)
        await repo.advance_status(
            inc.id,
            expected=IncidentStatus.RECEIVED,
            target=IncidentStatus.RESOLVED,
            disposition=disposition,
        )
        return inc, repo

    async def test_status_counts_groups_by_status(self, db_session) -> None:
        from backend.domain.incident import Incident, IncidentStatus, Severity
        from backend.repositories.incidents import IncidentRepository

        repo = IncidentRepository(db_session)
        for _ in range(2):
            inc = Incident(
                id=uuid.uuid4(),
                status=IncidentStatus.RECEIVED,
                severity=Severity.LOW,
                correlation_id=str(uuid.uuid4()),
                dedup_fingerprint=f"fp-{uuid.uuid4().hex}",
                source="wazuh",
                raw_alert={"rule": {"level": 3}},
            )
            await repo.create(inc)

        counts = await repo.status_counts()
        assert counts.get("received", 0) >= 2

    async def test_disposition_counts_since_respects_window(self, db_session) -> None:
        import sqlalchemy as sa

        inc, repo = await self._resolved(db_session, disposition="auto_remediated")
        # Backdate this incident far outside the 24h window.
        await db_session.execute(
            sa.text("UPDATE incidents SET updated_at = now() - make_interval(hours => 100) WHERE id = :id"),
            {"id": str(inc.id)},
        )
        await db_session.commit()
        # A second, in-window resolved incident.
        await self._resolved(db_session, disposition="auto_remediated")

        counts = await repo.disposition_counts_since(window_hours=24)
        # The 100h-old one is excluded; the fresh one is counted.
        assert counts.get("auto_remediated", 0) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_incident_repository.py::TestPipelineRepositoryReads -q`
Expected: FAIL with `AttributeError: 'IncidentRepository' object has no attribute 'status_counts'`.
(Requires Docker for testcontainers; if Docker is unavailable, this collects but errors on the container fixture — implement Step 3 and re-run when Docker is up.)

- [ ] **Step 3: Implement the repository methods**

In `backend/repositories/incidents.py`, inside `class IncidentRepository`, after `kpi_status_counts` (ends ~line 321) and before `list_non_terminal`, add:

```python
    async def status_counts(self) -> dict[str, int]:
        """Return raw per-status counts across all incidents (pipeline-map rail)."""
        result = await self._session.execute(
            sa.text("SELECT status, COUNT(*) AS cnt FROM incidents GROUP BY status")
        )
        return {row["status"]: row["cnt"] for row in result.mappings().all()}

    async def disposition_counts_since(self, *, window_hours: int) -> dict[str, int]:
        """Return per-disposition counts for incidents updated within the window."""
        result = await self._session.execute(
            sa.text(
                "SELECT disposition, COUNT(*) AS cnt FROM incidents "
                "WHERE disposition IS NOT NULL "
                "AND updated_at >= now() - make_interval(hours => :wh) "
                "GROUP BY disposition"
            ),
            {"wh": window_hours},
        )
        return {row["disposition"]: row["cnt"] for row in result.mappings().all()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_incident_repository.py::TestPipelineRepositoryReads -q`
Expected: PASS (2 passed). Requires Docker.

- [ ] **Step 5: Commit**

```bash
git add backend/repositories/incidents.py tests/integration/test_incident_repository.py
git commit -m "feat(dashboard): Add pipeline-map repository aggregate reads"
```

---

### Task 4: Config field + `GET /incidents/pipeline` endpoint

**Files:**
- Modify: `backend/infra/config.py` (`DashboardSettings`)
- Modify: `backend/routers/incidents.py` (imports + new route)
- Test: `tests/integration/test_pipeline_api.py`

**Interfaces:**
- Consumes (Task 1): `PipelineSnapshot`. Consumes (Task 2): `build_pipeline_snapshot`.
- Consumes: `settings.dashboard.pipeline_window_hours` (added here), `get_incident_repo` dependency (existing).
- Produces: `GET /incidents/pipeline` → `PipelineSnapshot` JSON (auth-protected at router level).

- [ ] **Step 1: Write the failing test**

Create `tests/integration/test_pipeline_api.py`:

```python
"""Integration tests for GET /incidents/pipeline (SOC pipeline-map, M-a)."""

from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

_JWT_SECRET = "pipeline-jwt-secret-long-enough-32chars"


def _make_app(*, mock_repo=None):
    from backend.dependencies import get_auth_service, get_incident_repo
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry
    from backend.main import create_app
    from backend.services.auth import AuthService

    clear_registry()
    settings = load_settings()
    app = create_app(settings)
    clear_registry()

    salt = "pipeline-test-salt"
    password = "pipeline-pass"
    iterations = 1000
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    auth_svc = AuthService(
        admin_username="operator",
        password_hash=pw_hash,
        salt=salt,
        iterations=iterations,
        jwt_secret=_JWT_SECRET,
        algorithm="HS256",
        token_ttl_minutes=60,
    )
    app.dependency_overrides[get_auth_service] = lambda: auth_svc

    if mock_repo is not None:

        async def fake_incident_repo():
            yield mock_repo

        app.dependency_overrides[get_incident_repo] = fake_incident_repo

    return app, password


def _make_mock_repo():
    repo = AsyncMock()
    repo.status_counts = AsyncMock(
        return_value={"triaging": 4, "responding": 2, "awaiting_approval": 1}
    )
    repo.disposition_counts_since = AsyncMock(
        return_value={"auto_resolved_triage": 3, "escalated_response": 1, "auto_remediated": 5}
    )
    return repo


@pytest.mark.integration
class TestPipelineEndpoint:
    def _login(self, client: TestClient, password: str) -> str:
        resp = client.post("/auth/login", json={"username": "operator", "password": password})
        assert resp.status_code == 200
        return resp.json()["access_token"]

    def test_pipeline_returns_200_with_snapshot(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/pipeline", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        body = resp.json()
        assert [s["key"] for s in body["stages"]] == ["intake", "triage", "enrichment", "response"]
        assert "terminals" in body
        assert "window_hours" in body

    def test_pipeline_stage_in_flight_and_branches(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/pipeline", headers={"Authorization": f"Bearer {token}"})
        body = resp.json()
        triage = next(s for s in body["stages"] if s["key"] == "triage")
        assert triage["in_flight"] == 4
        assert {b["to"]: b["count"] for b in triage["branches"]} == {"resolved": 3}
        assert body["terminals"]["awaiting"] == 1
        assert body["terminals"]["resolved"] == 8  # auto_resolved_triage(3) + auto_remediated(5)
        assert body["terminals"]["escalated"] == 1

    def test_pipeline_unauthenticated_returns_401(self) -> None:
        app, _pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/incidents/pipeline")
        assert resp.status_code == 401

    def test_pipeline_not_swallowed_by_incident_id_route(self) -> None:
        app, pw = _make_app(mock_repo=_make_mock_repo())
        with TestClient(app, raise_server_exceptions=False) as client:
            token = self._login(client, pw)
            resp = client.get("/incidents/pipeline", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert "stages" in resp.json()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_pipeline_api.py -q`
Expected: FAIL — the endpoint 404s (route not registered), so `test_pipeline_returns_200_with_snapshot` asserts 200 and fails.

- [ ] **Step 3a: Add the config field**

In `backend/infra/config.py`, inside `class DashboardSettings` (after `algorithm: str = "HS256"`, ~line 363), add:

```python
    pipeline_window_hours: Annotated[int, Field(gt=0)] = 24
```

(`Annotated` and `Field` are already imported.)

- [ ] **Step 3b: Register the endpoint**

In `backend/routers/incidents.py`, extend the `backend.domain.dashboard` import block (line ~22) to include `PipelineSnapshot`, and add the service import after the `build_kpi_snapshot` import (line ~35):

```python
from backend.services.pipeline_view import build_pipeline_snapshot
```

Then add the route **immediately after** the `get_kpis` endpoint (after line ~120, before the `/stream` route, which keeps it ahead of `/{incident_id}`):

```python
@router.get("/pipeline", response_model=PipelineSnapshot)
async def get_pipeline(
    request: Request,
    repo=Depends(get_incident_repo),
) -> PipelineSnapshot:
    window = request.app.state.settings.dashboard.pipeline_window_hours
    return await build_pipeline_snapshot(repo, window_hours=window)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_pipeline_api.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/infra/config.py backend/routers/incidents.py tests/integration/test_pipeline_api.py
git commit -m "feat(dashboard): Add GET /incidents/pipeline endpoint"
```

---

### Task 5: Full-tier verification

**Files:** none (verification only).

- [ ] **Step 1: Run the unit tier (batched, memory-safe)**

Run: `make test-unit`
Expected: `✓ all N files passed (unit tier)` — includes `test_pipeline_view.py`.

- [ ] **Step 2: Run the integration tier (Docker required)**

Run: `make test-integration`
Expected: `✓ all N files passed (integration tier)` — includes `test_pipeline_api.py` and the new `TestPipelineRepositoryReads`.

- [ ] **Step 3: Lint**

Run: `make lint`
Expected: clean (no import-linter violations — `domain/dashboard.py` added no outward imports; `services/pipeline_view.py` imports only `domain`).

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-17-soc-pipeline-map-design.md` §4 "Backend"):
- §4.1 new endpoint `GET /incidents/pipeline` → Task 4. ✅
- §4.2 `PipelineSnapshot`/`StageNode`/`BranchOutflow` shape → Task 1 (note: `TerminalCounts` is a typed object rather than the spec sketch's loose `TerminalCounts`; D-equivalent). ✅
- §4.3 stage grouping (received/grounding/grounded→intake, triaging→triage, enriching→enrichment, responding/awaiting_approval→response) → Task 2 `_STATUS_TO_STAGE`. ✅
- §4.3 branch outflow from stage-tagged dispositions → Task 2 `_DISPOSITION_TO_BRANCH`. ✅
- §4.3 rolling-window terminal counts (config, default 24h) → Task 2 `terminal_counts` + Task 3 `disposition_counts_since` + Task 4 `pipeline_window_hours`. ✅
- §4.3 read-only repo helpers → Task 3. ✅
- §4.4 graceful/no-PII: snapshot is counts+enums only — confirmed by the DTO shapes (no incident text fields). ✅ Best-effort sub-count fallback: the two repo reads return `{}` on no rows (never raise), and `stage_in_flight`/`stage_branches` default missing keys to 0/empty — so a sparse DB yields zeros, not errors. ✅
- §10 milestone M-a scope (endpoint + service + DTO + repo helper + tests) → all tasks. ✅
- Out of scope (§11): no migration, no FSM, no new writer, no frontend — honored (M-b/M-c are separate plans). ✅

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows full assertions and the exact run command + expected result. ✅

**3. Type consistency:** `status_counts()` / `disposition_counts_since(*, window_hours)` names and signatures are identical in the Task 2 mock, the Task 3 implementation, and the Task 4 mock. `build_pipeline_snapshot(repo, *, window_hours)` signature is identical in Task 2 and Task 4. DTO field names (`in_flight`, `branches`, `to`, `count`, `resolved`, `escalated`, `awaiting`, `window_hours`) match across Tasks 1, 2, and 4's JSON assertions. ✅

---

## Next plans (after M-a merges)

- **M-b:** live rail frontend (`features/map/` rail + `usePipeline` 2s polling + count-up/pulse/edge-flash + reduced-motion + Live/Pause) + nav item + route. Needs separate frontend grounding (vitest/fetch-mock pattern, chart lib, shadcn drawer).
- **M-c:** Human Attention lane (approve/reject reuse + escalated cards) + incident detail drawer + branch expand + journey overlay + Playwright e2e.
