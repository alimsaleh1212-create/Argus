"""Integration tests for record_outcome_facts against real memory (US1, T005).

Requires a real Graphiti/Neo4j memory store. Skips gracefully when unavailable.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from backend.domain.incident import Incident, IncidentStatus, Severity
from backend.domain.memory import EntityKind, EntityRef
from backend.domain.redaction import Boundary
from backend.infra.config import FeedbackSettings

pytestmark = pytest.mark.integration

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


class _FakeRedactor:
    def redact_text(self, text: str, boundary: Boundary) -> str:
        return text

    def redact_mapping(self, data: dict, boundary: Boundary) -> dict:
        return data


def _incident(verdict: str, targets: list[str], updated_at: datetime) -> Incident:
    return Incident(
        id=uuid.uuid4(),
        status=IncidentStatus.ESCALATED,
        severity=Severity.HIGH,
        correlation_id="corr",
        dedup_fingerprint="fp",
        source="wazuh",
        raw_alert={},
        evidence={
            "response": {
                "results": [
                    {"type": "block_ip", "target": t, "status": "applied"}
                    for t in targets
                ],
                "verification": {"verdict": verdict},
            }
        },
        updated_at=updated_at,
    )


@pytest.fixture(scope="module")
def neo4j_container():
    try:
        from testcontainers.neo4j import Neo4jContainer
    except ImportError:
        pytest.skip("testcontainers[neo4j] not installed")

    with Neo4jContainer(image="neo4j:5.26", password="test-password") as container:
        yield container


@pytest.fixture(scope="module")
async def graphiti_memory(neo4j_container):
    if not GEMINI_API_KEY:
        pytest.skip("GEMINI_API_KEY not set — skipping Graphiti integration tests")

    try:
        from graphiti_core import Graphiti
        from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
        from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
    except ImportError:
        pytest.skip("graphiti-core not installed")

    from backend.infra.config import MemorySettings
    from backend.infra.memory import GraphitiMemory

    bolt_url = neo4j_container.get_connection_url()
    llm = GeminiClient(config=LLMConfig(api_key=GEMINI_API_KEY))
    embedder = GeminiEmbedder(
        config=GeminiEmbedderConfig(api_key=GEMINI_API_KEY, embedding_model="text-embedding-004")
    )
    graphiti = Graphiti(
        uri=bolt_url,
        user="neo4j",
        password="test-password",
        llm_client=llm,
        embedder=embedder,
    )
    await graphiti.build_indices_and_constraints()
    settings = MemorySettings(
        enabled=True,
        backend="graphiti",
        neo4j_uri=bolt_url,
        retrieval_timeout_s=30.0,
        write_timeout_s=60.0,
    )
    mem = GraphitiMemory(graphiti=graphiti, settings=settings)
    yield mem
    await graphiti.close()


@pytest.mark.asyncio
async def test_write_then_query_round_trip(graphiti_memory) -> None:
    from backend.services.memory import record_outcome_facts

    t1 = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    incident = _incident("regressed", ["198.51.100.7"], updated_at=t1)
    cfg = FeedbackSettings()

    await record_outcome_facts(incident, graphiti_memory, _FakeRedactor(), cfg=cfg)

    entity = EntityRef(kind=EntityKind.ADDRESS, value="198.51.100.7")
    state = await graphiti_memory.query_fact(entity, cfg.outcome_fact_type)
    assert state.fact is not None
    assert state.fact.value == "regressed"
    assert state.is_current is True


@pytest.mark.asyncio
async def test_contradicting_outcome_supersedes_prior(graphiti_memory) -> None:
    from backend.services.memory import record_outcome_facts

    t1 = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    t2 = t1 + timedelta(hours=2)
    cfg = FeedbackSettings()
    target = "198.51.100.8"

    incident1 = _incident("regressed", [target], updated_at=t1)
    await record_outcome_facts(incident1, graphiti_memory, _FakeRedactor(), cfg=cfg)

    incident2 = _incident("verified", [target], updated_at=t2)
    await record_outcome_facts(incident2, graphiti_memory, _FakeRedactor(), cfg=cfg)

    entity = EntityRef(kind=EntityKind.ADDRESS, value=target)
    state_now = await graphiti_memory.query_fact(entity, cfg.outcome_fact_type)
    state_t1 = await graphiti_memory.query_fact(entity, cfg.outcome_fact_type, as_of=t1)

    assert state_now.fact is not None
    assert state_now.fact.value == "verified"
    assert state_now.is_current is True
    assert state_t1.fact is not None
    assert state_t1.fact.value == "regressed"
    assert state_t1.is_current is False


@pytest.mark.asyncio
async def test_memory_outage_swallows_error() -> None:
    from backend.services.memory import record_outcome_facts

    class _BrokenStore:
        async def write_fact(self, fact) -> None:
            raise RuntimeError("memory down")

    incident = _incident("regressed", ["198.51.100.9"], datetime.now(UTC))
    cfg = FeedbackSettings()

    # Caller (worker) wraps in try/except; the function itself should not raise.
    try:
        await record_outcome_facts(incident, _BrokenStore(), _FakeRedactor(), cfg=cfg)
    except Exception as exc:
        pytest.fail(f"record_outcome_facts raised on memory outage: {exc}")


@pytest.mark.asyncio
async def test_refinalize_is_idempotent(graphiti_memory) -> None:
    from backend.services.memory import record_outcome_facts

    t1 = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    incident = _incident("regressed", ["198.51.100.10"], updated_at=t1)
    cfg = FeedbackSettings()

    await record_outcome_facts(incident, graphiti_memory, _FakeRedactor(), cfg=cfg)
    await record_outcome_facts(incident, graphiti_memory, _FakeRedactor(), cfg=cfg)

    entity = EntityRef(kind=EntityKind.ADDRESS, value="198.51.100.10")
    state = await graphiti_memory.query_fact(entity, cfg.outcome_fact_type)
    assert state.is_current is True
    # No spurious supersession from identical re-write.
    assert state.has_superseded is False
