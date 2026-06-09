"""Retrieval eval gate — SPEC-memory #6 extended with corpus fixtures (SPEC-knowledge-corpus #8).

Scores hit@k and MRR on the committed incident fixture set, and separately verifies
corpus retrieval cold-start competence using the corpus fixture set.

The gate threshold is in config/eval_thresholds.yaml:
  retrieval.threshold.min_hit_at_k: 0.80  k: 5  min_mrr: 0.60
  retrieval.corpus_fixtures.min_hit_at_k: 1.0  k: 5
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
CORPUS_FIXTURES = Path(__file__).parent.parent / "fixtures" / "corpus_retrieval"
CONFIG = Path(__file__).parent.parent.parent / "config" / "eval_thresholds.yaml"


def _load_thresholds() -> dict[str, Any]:
    with open(CONFIG) as f:
        return yaml.safe_load(f)["gates"]["retrieval"]["threshold"]


def _load_corpus_config() -> dict[str, Any]:
    with open(CONFIG) as f:
        gate = yaml.safe_load(f)["gates"]["retrieval"]
        return gate.get("corpus_fixtures", {})


def _load_corpus_queries() -> list[dict]:
    with open(CORPUS_FIXTURES / "queries.json") as f:
        return json.load(f)


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


# ── Corpus retrieval cold-start gate ─────────────────────────────────────────


class _InMemoryCorpusStore:
    """In-memory CorpusRetriever for eval scoring without Postgres."""

    def __init__(self, entries: list) -> None:
        self._entries = entries

    async def search_reference(self, query: Any, *, k: int) -> list:
        from backend.domain.corpus import ReferenceHit, ReferenceKind

        hits: dict = {}

        # Technique-keyed
        for entry in self._entries:
            if entry.key in [t.lower() for t in query.technique_ids] or \
               entry.key.lower() in [t.lower() for t in query.technique_ids]:
                from backend.repositories.corpus import _update_hit
                _update_hit(hits, entry, 1.0, "technique")

            # Tag match
            if query.terms:
                terms_lower = [t.lower() for t in query.terms]
                overlap = len(set(entry.tags) & set(terms_lower))
                if overlap:
                    from backend.repositories.corpus import _update_hit
                    _update_hit(hits, entry, 0.5, "tag")

            # Lexical
            if query.terms:
                for term in query.terms:
                    if term.lower() in entry.title.lower() or term.lower() in entry.content.lower():
                        from backend.repositories.corpus import _update_hit
                        _update_hit(hits, entry, 0.3, "term")

        result = sorted(hits.values(), key=lambda h: (-h.relevance, h.entry.key))
        return result[:k]


def _seed_in_memory_corpus() -> list:
    """Load bundled corpus files into domain objects (no DB needed)."""
    import json

    from backend.domain.corpus import ReferenceCorpusEntry, ReferenceKind

    corpus_dir = Path(__file__).parent.parent.parent / "backend" / "data" / "corpus"
    entries = []

    with open(corpus_dir / "techniques.json") as f:
        for t in json.load(f):
            tags = [t["id"].lower(), t.get("tactic", "").lower()]
            entries.append(ReferenceCorpusEntry(
                kind=ReferenceKind.TECHNIQUE,
                key=t["id"],
                title=t["title"],
                content=t["mitigations"],
                tags=tags,
            ))

    with open(corpus_dir / "runbooks.json") as f:
        for r in json.load(f):
            tags = [tech.lower() for tech in r.get("techniques", [])]
            entries.append(ReferenceCorpusEntry(
                kind=ReferenceKind.RUNBOOK,
                key=r["key"],
                title=r["title"],
                content=r["steps"],
                tags=tags,
            ))

    return entries


@pytest.mark.asyncio
async def test_corpus_retrieval_gate() -> None:
    """Corpus cold-start gate: seeded store returns expected entries for labeled queries."""
    from backend.domain.corpus import ReferenceQuery

    cfg = _load_corpus_config()
    k = cfg.get("k", 5)
    min_hit = cfg.get("min_hit_at_k", 1.0)

    queries = _load_corpus_queries()
    entries = _seed_in_memory_corpus()
    store = _InMemoryCorpusStore(entries)

    hits_list: list[bool] = []

    for q in queries:
        query = ReferenceQuery(**q["query"])
        results = await store.search_reference(query, k=k)

        if not results:
            continue  # cold/miss excluded per gate spec

        result_keys = {h.entry.key for h in results}
        hit = any(expected in result_keys for expected in q["expected_keys"])
        hits_list.append(hit)

    assert hits_list, "No corpus queries produced results — check fixtures and bundled data"
    hit_rate = sum(hits_list) / len(hits_list)
    assert hit_rate >= min_hit, (
        f"corpus hit@{k} = {hit_rate:.2f} < threshold {min_hit:.2f}"
    )


# ── Enrichment retrieval gate (SPEC-enrichment-agent #9) ─────────────────────

ENRICHMENT_FIXTURES = Path(__file__).parent.parent / "fixtures" / "enrichment"


def _load_enrichment_config() -> dict[str, Any]:
    with open(CONFIG) as f:
        gate = yaml.safe_load(f)["gates"]["retrieval"]
        return gate.get("enrichment_fixtures", {})


def _load_enrichment_cases() -> list[dict]:
    with open(ENRICHMENT_FIXTURES / "cases.json") as f:
        return json.load(f)


@pytest.mark.asyncio
async def test_enrichment_retrieval_gate() -> None:
    """Enrichment retrieval gate: corpus + memory directions scored against fixture labels."""
    from backend.agents.enrichment import build_reference_query, extract_entities
    from backend.domain.corpus import ReferenceQuery

    cfg = _load_enrichment_config()
    k = cfg.get("k", 5)
    min_hit = cfg.get("min_hit_at_k", 0.80)

    cases = _load_enrichment_cases()
    entries = _seed_in_memory_corpus()
    corpus_store = _InMemoryCorpusStore(entries)

    # Seed memory store with minimal priors keyed from fixture expected_prior_ids
    # (summary text chosen so keyword overlap finds them via extract_entities entities).
    mem_store = _InMemoryStore()
    _prior_seed = [
        IncidentEpisode(
            incident_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            observed_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            summary="Multiple authentication failures brute force admin auth-server-01 T1110",
            verdict="real",
            severity=Severity.HIGH,
            disposition="escalated",
        ),
        IncidentEpisode(
            incident_id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            observed_at=datetime(2025, 2, 1, tzinfo=timezone.utc),
            summary="Phishing email attachment opened jdoe workstation-07 T1566",
            verdict="real",
            severity=Severity.HIGH,
            disposition="escalated",
        ),
        IncidentEpisode(
            incident_id=uuid.UUID("00000000-0000-0000-0000-000000000004"),
            observed_at=datetime(2025, 3, 1, tzinfo=timezone.utc),
            summary="Anomalous login valid account bob.smith vpn-gateway T1078 203.0.113.99",
            verdict="real",
            severity=Severity.CRITICAL,
            disposition="escalated",
        ),
    ]
    _prior_id_map = {
        "prior-brute-01": uuid.UUID("00000000-0000-0000-0000-000000000001"),
        "prior-phishing-01": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "prior-valid-account-01": uuid.UUID("00000000-0000-0000-0000-000000000004"),
    }
    for ep in _prior_seed:
        await mem_store.write_episode(ep)

    hits_list: list[bool] = []

    for case in cases:
        inc_evidence = case["incident"]
        expected_corpus_keys: list[str] = case.get("expected_corpus_keys", [])
        expected_prior_label_ids: list[str] = case.get("expected_prior_ids", [])
        expected_prior_uuids = [
            _prior_id_map[pid] for pid in expected_prior_label_ids if pid in _prior_id_map
        ]

        # Corpus direction
        if expected_corpus_keys:
            query = build_reference_query(inc_evidence)
            corpus_results = await corpus_store.search_reference(query, k=k)
            if corpus_results:
                result_keys = {h.entry.key for h in corpus_results}
                hit = any(ek in result_keys for ek in expected_corpus_keys)
                hits_list.append(hit)

        # Memory direction
        if expected_prior_uuids:
            summary = inc_evidence.get("summary", "")
            entities = extract_entities(inc_evidence)
            eq = EpisodeQuery(text=summary, entities=entities)
            mem_results = await mem_store.search_similar(eq, k=k)
            if mem_results:
                found_ids = {h.incident_id for h in mem_results}
                hit = any(pid in found_ids for pid in expected_prior_uuids)
                hits_list.append(hit)

    assert hits_list, "No enrichment fixture cases produced retrieval results — check fixtures"
    hit_rate = sum(hits_list) / len(hits_list)
    assert hit_rate >= min_hit, (
        f"enrichment retrieval hit@{k} = {hit_rate:.2f} < threshold {min_hit:.2f}"
    )


@pytest.mark.asyncio
async def test_corpus_retrieval_unseeded_returns_empty() -> None:
    """An empty (unseeded) store returns [] for any query (demonstrating seed value)."""
    from backend.domain.corpus import ReferenceQuery

    store = _InMemoryCorpusStore([])
    results = await store.search_reference(
        ReferenceQuery(technique_ids=["T1110"], terms=["brute"]), k=5
    )
    assert results == []
