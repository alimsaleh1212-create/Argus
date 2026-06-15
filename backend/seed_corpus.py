"""One-shot corpus seeder — mirrors `migrate` in the compose stack.

Usage: python -m backend.seed_corpus

Loads the bundled corpus files under CorpusSettings.data_dir, redacts text at
the MEMORY_WRITE boundary, upserts reference rows (idempotent on kind+key), and
writes seed IOC reputation facts via store.write_fact (no-op if Neo4j is down).

Exit codes:
  0 — success (including partial — individual bad entries are skipped, not fatal)
  1 — unrecoverable error (e.g. Postgres unreachable)
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.infra.config import load_settings
from backend.infra.logging import configure_logging, get_logger
from backend.infra.memory import NullMemory
from backend.infra.redaction import build_redactor
from backend.repositories.corpus import CorpusRepository
from backend.services.corpus import seed_reference, seed_reputation

logger = get_logger(__name__)


def _load_json(path: Path) -> list | dict:
    with path.open() as f:
        return json.load(f)


async def _run() -> None:
    configure_logging()

    settings = load_settings()
    corpus_cfg = settings.corpus
    data_dir = Path(corpus_cfg.data_dir)

    if not data_dir.exists():
        logger.error("corpus_data_dir_missing", path=str(data_dir))
        sys.exit(1)

    # Build Redactor (observability settings are the same as the API uses)
    redactor = build_redactor(settings.observability)

    # DB session
    dsn = settings.postgres.dsn.get_secret_value()
    engine = create_async_engine(dsn, pool_pre_ping=True, echo=False)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

    # Memory store — try Graphiti; degrade to NullMemory if unavailable
    store = await _build_store(settings)

    try:
        async with factory() as session:
            repo = CorpusRepository(session)

            # Load reference files
            records: dict[str, list] = {}
            for name in ("techniques", "runbooks"):
                path = data_dir / f"{name}.json"
                if path.exists():
                    try:
                        records[name] = _load_json(path)  # type: ignore[assignment]
                        logger.info("corpus_file_loaded", file=str(path), count=len(records[name]))
                    except Exception as exc:
                        logger.warning("corpus_file_load_error", file=str(path), error=str(exc))
                        records[name] = []
                else:
                    logger.warning("corpus_file_missing", file=str(path))
                    records[name] = []

            await seed_reference(records, redactor, repo)

            # Load IOC reputation
            ioc_path = data_dir / "ioc_reputation.json"
            if ioc_path.exists():
                try:
                    ioc_records = _load_json(ioc_path)
                    assert isinstance(ioc_records, list)
                    await seed_reputation(ioc_records, redactor, store)
                except Exception as exc:
                    logger.warning("corpus_ioc_load_error", error=str(exc))
            else:
                logger.warning("corpus_ioc_file_missing", file=str(ioc_path))

        logger.info("seed_corpus_complete")
    except Exception as exc:
        logger.error("seed_corpus_failed", error=str(exc))
        sys.exit(1)
    finally:
        await engine.dispose()
        if hasattr(store, "close"):
            try:
                await store.close()
            except Exception:
                pass


async def _build_store(settings) -> object:  # type: ignore[type-arg]
    """Try to build GraphitiMemory; fall back to NullMemory on any error."""
    mem_cfg = settings.memory
    if not mem_cfg.enabled:
        return NullMemory()
    try:
        from graphiti_core import Graphiti

        from backend.infra.memory import (
            GraphitiMemory,
            _needs_gemini,
            build_cross_encoder,
            build_embedder,
            build_llm_client,
        )
        from backend.infra.vault import VaultClient

        # Standalone script: no lifespan container, so build a client and fetch
        # the paths it needs on demand.
        vault = VaultClient(settings.vault, settings.startup)
        creds = await vault.fetch_secret(mem_cfg.neo4j_vault_path)
        neo4j_user = creds.get("username", "neo4j")
        neo4j_password = creds.get("password", "")
        neo4j_uri = creds.get("uri", mem_cfg.neo4j_uri)

        # The Gemini key is needed only if the embedder, reranker, or an LLM
        # fallback slot uses gemini.
        gemini_key = ""
        if _needs_gemini(mem_cfg, settings.llm):
            gemini_key = (await vault.fetch_secret(settings.llm.gemini_vault_path)).get("api_key", "")

        # Same provider selection as the worker (shared builders) — keeps seeded
        # vectors compatible with what the worker writes at runtime.
        embedder = build_embedder(mem_cfg, gemini_key=gemini_key)
        llm_client = build_llm_client(mem_cfg, settings.llm, gemini_key=gemini_key)
        cross_encoder = build_cross_encoder(mem_cfg, settings.llm, gemini_key=gemini_key)
        graphiti = Graphiti(
            uri=neo4j_uri,
            user=neo4j_user,
            password=neo4j_password,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=cross_encoder,
        )
        await graphiti.build_indices_and_constraints()
        return GraphitiMemory(graphiti=graphiti, settings=mem_cfg)
    except Exception as exc:
        logger.warning("seed_corpus_memory_unavailable", error=str(exc))
        return NullMemory()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
