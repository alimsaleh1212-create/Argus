"""Milestone 0 — Graphiti go/no-go spike.

Writes a handful of sample-incident episodes to Neo4j via Graphiti, retrieves
a similar one, forces a fact conflict to observe native invalidation, and
prints write/retrieval latency + token cost.

Usage:
    docker compose up -d neo4j
    uv run python -m scripts.memory_spike

Records the go/no-go decision in DECISIONS.md (Component 007).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import UTC, datetime

SAMPLE_EPISODES = [
    {
        "name": "spike-001",
        "body": json.dumps({
            "incident_id": "spike-001",
            "summary": "SSH brute-force attempt from 203.0.113.10 against web-server-01",
            "verdict": "real",
            "severity": "high",
            "disposition": "escalated_enrichment",
            "entities": [
                {"kind": "address", "value": "203.0.113.10"},
                {"kind": "host", "value": "web-server-01"},
            ],
        }),
        "reference_time": datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
    },
    {
        "name": "spike-002",
        "body": json.dumps({
            "incident_id": "spike-002",
            "summary": "Repeated failed login from 203.0.113.10 against db-server-02",
            "verdict": "real",
            "severity": "medium",
            "disposition": "auto_resolved_triage",
            "entities": [
                {"kind": "address", "value": "203.0.113.10"},
                {"kind": "host", "value": "db-server-02"},
            ],
        }),
        "reference_time": datetime(2024, 1, 15, 11, 0, 0, tzinfo=UTC),
    },
    {
        "name": "spike-003",
        "body": json.dumps({
            "incident_id": "spike-003",
            "summary": "Routine health check probe on port 22 — known scanner",
            "verdict": "noise",
            "severity": "low",
            "disposition": "auto_resolved_noise",
            "entities": [
                {"kind": "address", "value": "192.0.2.50"},
                {"kind": "host", "value": "honeypot-01"},
            ],
        }),
        "reference_time": datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC),
    },
]

CONFLICT_EPISODES = [
    {
        "name": "spike-conflict-t1",
        "body": json.dumps({
            "incident_id": "spike-conflict-t1",
            "summary": "Address 198.51.100.5 assessed as benign scanner",
            "verdict": "noise",
            "entity_fact": {"entity": "198.51.100.5", "reputation": "benign"},
        }),
        "reference_time": datetime(2024, 1, 16, 9, 0, 0, tzinfo=UTC),
    },
    {
        "name": "spike-conflict-t2",
        "body": json.dumps({
            "incident_id": "spike-conflict-t2",
            "summary": "Address 198.51.100.5 now confirmed malicious C2 node",
            "verdict": "real",
            "entity_fact": {"entity": "198.51.100.5", "reputation": "malicious"},
        }),
        "reference_time": datetime(2024, 1, 16, 14, 0, 0, tzinfo=UTC),
    },
]


async def run_spike() -> None:
    try:
        from graphiti_core import Graphiti
        from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
        from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
    except ImportError as e:
        print(f"[SPIKE] Import error: {e}. Ensure graphiti-core[google-genai] is installed.")
        return

    neo4j_uri = os.getenv("ARGUS__MEMORY__NEO4J_URI", "bolt://localhost:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "dev-neo4j-password")
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")

    if not gemini_api_key:
        print("[SPIKE] No GEMINI_API_KEY — set it in .env. Aborting.")
        return

    print(f"[SPIKE] Connecting to Neo4j at {neo4j_uri}...")
    llm_client = GeminiClient(config=LLMConfig(api_key=gemini_api_key))
    embedder = GeminiEmbedder(config=GeminiEmbedderConfig(api_key=gemini_api_key))

    graphiti = Graphiti(
        uri=neo4j_uri,
        user=neo4j_user,
        password=neo4j_password,
        llm_client=llm_client,
        embedder=embedder,
    )

    try:
        await graphiti.build_indices_and_constraints()
        print("[SPIKE] Indices built.")

        print("\n=== Writing sample episodes ===")
        write_times = []
        for ep in SAMPLE_EPISODES:
            t0 = time.perf_counter()
            await graphiti.add_episode(
                name=ep["name"],
                episode_body=ep["body"],
                source_description="argus-spike",
                reference_time=ep["reference_time"],
            )
            elapsed = (time.perf_counter() - t0) * 1000
            write_times.append(elapsed)
            print(f"  write {ep['name']}: {elapsed:.1f} ms")

        avg_write = sum(write_times) / len(write_times)
        print(f"\n  avg write latency: {avg_write:.1f} ms  (p95: {sorted(write_times)[int(len(write_times)*0.95)]:.1f} ms)")

        print("\n=== Similarity retrieval ===")
        query = "SSH login failure attack on server"
        t0 = time.perf_counter()
        results = await graphiti.search(query, num_results=3)
        retrieve_ms = (time.perf_counter() - t0) * 1000
        print(f"  query: '{query}'")
        print(f"  retrieval latency: {retrieve_ms:.1f} ms  ({len(results)} results)")
        for r in results:
            print(f"    - {r.name if hasattr(r, 'name') else r}")

        print("\n=== Conflict / temporal invalidation ===")
        for ep in CONFLICT_EPISODES:
            t0 = time.perf_counter()
            await graphiti.add_episode(
                name=ep["name"],
                episode_body=ep["body"],
                source_description="argus-spike-conflict",
                reference_time=ep["reference_time"],
            )
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"  write {ep['name']}: {elapsed:.1f} ms")

        print("  Conflict written — verify native invalidation in Neo4j Browser:")
        print("    MATCH (n)-[r]->(m) WHERE r.invalid_at IS NOT NULL RETURN r.fact, r.valid_at, r.invalid_at")

        print("\n=== GO / NO-GO ===")
        threshold_write_ms = 5000
        threshold_retrieve_ms = 3000
        go = avg_write < threshold_write_ms and retrieve_ms < threshold_retrieve_ms
        verdict = "GO — Graphiti+Neo4j accepted" if go else "NO-GO — consider pgvector fallback"
        print(f"  avg write: {avg_write:.0f} ms  (threshold: {threshold_write_ms} ms)")
        print(f"  retrieval: {retrieve_ms:.0f} ms  (threshold: {threshold_retrieve_ms} ms)")
        print(f"  VERDICT: {verdict}")
        print("\nRecord this result in DECISIONS.md under Component 007 / MD0.")

    finally:
        await graphiti.close()


def main() -> None:
    asyncio.run(run_spike())


if __name__ == "__main__":
    main()
