"""Retrieval eval gate — SPEC-memory #6 / T021.

Scores hit@k and MRR on the committed labeled fixture set.

In CI (no live Graphiti), the gate is exercised against a pre-populated in-memory
store so the fixture loading, scoring logic, and threshold checks are proven.
Full live scoring against a real Neo4j runs in the integration tier.

The gate threshold is in config/eval_thresholds.yaml:
  retrieval.threshold.min_hit_at_k: 0.80  k: 5  min_mrr: 0.60
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.domain.incident import Severity
from backend.domain.memory import (
    EpisodeQuery,
    IncidentEpisode,
    MemoryHit,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "memory_retrieval"
CONFIG = Path(__file__).parent.parent.parent / "config" / "eval_thresholds.yaml"


def _load_thresholds() -> dict[str, Any]:
    with open(CONFIG) as f:
        return yaml.safe_load(f)["gates"]["retrieval"]["threshold"]


def _load_priors() -> list[dict]:
    with open(FIXTURES / "priors.json") as f:
        return json.load(f)


def _load_queries() -> list[dict]:
    with open(FIXTURES / "queries.json") as f:
        return json.load(f)


def _prior_to_episode(p: dict) -> IncidentEpisode:
    from backend.domain.memory import EntityRef, EntityKind

    entities = []
    for e in p.get("entities", []):
        entities.append(EntityRef(kind=EntityKind(e["kind"]), value=e["value"]))

    return IncidentEpisode(
        incident_id=uuid.UUID(p["incident_id"]),
        observed_at=datetime.fromisoformat(p["observed_at"].replace("Z", "+00:00")),
        summary=p["summary"],
        verdict=p["verdict"],
        severity=Severity(p["severity"]),
        disposition=p["disposition"],
        entities=entities,
        fields=p.get("fields", {}),
    )


class _InMemoryStore:
    """Minimal in-memory store for eval scoring without Neo4j."""

    def __init__(self) -> None:
        self._episodes: list[IncidentEpisode] = []

    async def write_episode(self, episode: IncidentEpisode) -> None:
        if not any(e.incident_id == episode.incident_id for e in self._episodes):
            self._episodes.append(episode)

    async def search_similar(self, query: EpisodeQuery, *, k: int) -> list[MemoryHit]:
        """Keyword-overlap similarity (deterministic, no embeddings needed in CI)."""
        query_words = set(query.text.lower().split())
        scored: list[tuple[float, IncidentEpisode]] = []
        for ep in self._episodes:
            summary_words = set(ep.summary.lower().split())
            overlap = len(query_words & summary_words)
            if overlap > 0:
                score = overlap / max(len(query_words), len(summary_words))
                scored.append((score, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        hits = []
        for rank, (score, ep) in enumerate(scored[:k]):
            hits.append(
                MemoryHit(
                    incident_id=ep.incident_id,
                    summary=ep.summary,
                    disposition=ep.disposition,
                    observed_at=ep.observed_at,
                    relevance=min(1.0, score),
                )
            )
        return hits


def _hit_at_k(results: list[MemoryHit], expected_id: uuid.UUID, k: int) -> bool:
    return any(h.incident_id == expected_id for h in results[:k])


def _reciprocal_rank(results: list[MemoryHit], expected_id: uuid.UUID) -> float:
    for rank, hit in enumerate(results, start=1):
        if hit.incident_id == expected_id:
            return 1.0 / rank
    return 0.0


@pytest.mark.asyncio
async def test_retrieval_gate() -> None:
    thresholds = _load_thresholds()
    k = thresholds["k"]
    min_hit_at_k = thresholds["min_hit_at_k"]
    min_mrr = thresholds["min_mrr"]

    priors = _load_priors()
    queries = _load_queries()

    store = _InMemoryStore()
    for p in priors:
        await store.write_episode(_prior_to_episode(p))

    hits_list: list[bool] = []
    rr_list: list[float] = []

    for q in queries:
        expected_id = uuid.UUID(q["expected_prior_incident_id"])
        results = await store.search_similar(EpisodeQuery(text=q["text"]), k=k)

        if not results:
            # Cold-start (empty results) — exclude from scoring, per spec
            continue

        h = _hit_at_k(results, expected_id, k)
        rr = _reciprocal_rank(results, expected_id)
        hits_list.append(h)
        rr_list.append(rr)

    assert hits_list, "No queries produced results — check fixtures"

    hit_rate = sum(hits_list) / len(hits_list)
    mrr = sum(rr_list) / len(rr_list)

    assert hit_rate >= min_hit_at_k, (
        f"hit@{k} = {hit_rate:.2f} < threshold {min_hit_at_k:.2f}"
    )
    assert mrr >= min_mrr, f"MRR = {mrr:.2f} < threshold {min_mrr:.2f}"
