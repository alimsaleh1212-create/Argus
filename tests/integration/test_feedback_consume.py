"""Integration tests for gather_feedback / bias consumption (US2, T010).

Seeds a remediation_outcome fact in real memory and asserts gather_feedback
returns (or drops) the expected FeedbackSignal.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from backend.domain.memory import EntityKind, EntityRef, TemporalFact
from backend.domain.redaction import Boundary
from backend.infra.config import FeedbackSettings
from backend.services.feedback import gather_feedback

pytestmark = pytest.mark.integration

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


class _FakeRedactor:
    def redact_text(self, text: str, boundary: Boundary) -> str:
        return text

    def redact_mapping(self, data: dict, boundary: Boundary) -> dict:
        return data


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
async def test_gather_feedback_returns_current_signal(graphiti_memory) -> None:
    cfg = FeedbackSettings()
    entity = EntityRef(kind=EntityKind.ADDRESS, value="198.51.100.20")
    fact = TemporalFact(
        entity=entity,
        fact_type=cfg.outcome_fact_type,
        value="regressed",
        valid_from=datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC),
    )
    await graphiti_memory.write_fact(fact)

    signals = await gather_feedback(memory=graphiti_memory, entities=[entity], cfg=cfg)

    assert len(signals) == 1
    assert signals[0].indicator == entity.value
    assert signals[0].outcome.value == "regressed"
    assert signals[0].is_current is True


@pytest.mark.asyncio
async def test_gather_feedback_drops_superseded(graphiti_memory) -> None:
    cfg = FeedbackSettings()
    entity = EntityRef(kind=EntityKind.ADDRESS, value="198.51.100.21")
    t1 = datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC)
    t2 = datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)

    await graphiti_memory.write_fact(
        TemporalFact(
            entity=entity,
            fact_type=cfg.outcome_fact_type,
            value="regressed",
            valid_from=t1,
        )
    )
    await graphiti_memory.write_fact(
        TemporalFact(
            entity=entity,
            fact_type=cfg.outcome_fact_type,
            value="verified",
            valid_from=t2,
        )
    )

    signals = await gather_feedback(memory=graphiti_memory, entities=[entity], cfg=cfg)
    assert len(signals) == 1
    assert signals[0].outcome.value == "verified"


@pytest.mark.asyncio
async def test_gather_feedback_bounded_by_max_indicators(graphiti_memory) -> None:
    cfg = FeedbackSettings(max_indicators=2)
    entities = [EntityRef(kind=EntityKind.ADDRESS, value=f"198.51.100.{i}") for i in range(10, 15)]
    for entity in entities:
        await graphiti_memory.write_fact(
            TemporalFact(
                entity=entity,
                fact_type=cfg.outcome_fact_type,
                value="unverified",
                valid_from=datetime(2024, 1, 15, 9, 0, 0, tzinfo=UTC),
            )
        )

    signals = await gather_feedback(memory=graphiti_memory, entities=entities, cfg=cfg)
    assert len(signals) == 2


@pytest.mark.asyncio
async def test_gather_feedback_outage_returns_empty() -> None:
    class _BrokenMemory:
        async def query_fact(self, entity, fact_type, *, as_of=None):
            raise RuntimeError("memory down")

    cfg = FeedbackSettings()
    entity = EntityRef(kind=EntityKind.ADDRESS, value="198.51.100.22")
    signals = await gather_feedback(memory=_BrokenMemory(), entities=[entity], cfg=cfg)
    assert signals == []
