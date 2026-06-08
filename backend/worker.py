"""Async worker — consumes the incident queue and runs grounding.

Entrypoint: python -m backend.worker
Same image as the API (one image, two containers).
"""

from __future__ import annotations

import asyncio
import uuid

from backend.infra.logging import get_logger

logger = get_logger(__name__)


async def _run(settings, queue, repo, tracer, supervisor=None) -> None:
    """Inner consume loop (extracted for testability)."""
    await queue.recover()
    logger.info("worker_started")

    while True:
        incident_id_str = await queue.dequeue()
        if not incident_id_str:
            continue

        incident_id = uuid.UUID(incident_id_str)

        try:
            from backend.infra.logging import bind_incident

            bind_incident(incident_id_str)

            claimed = await repo.claim_for_grounding(incident_id)
            if not claimed:
                logger.info("worker_skip_already_claimed", incident_id=incident_id_str)
                await queue.ack(incident_id_str)
                continue

            incident = await repo.get(incident_id)
            if incident is None:
                logger.warning("worker_incident_not_found", incident_id=incident_id_str)
                await queue.ack(incident_id_str)
                continue

            from backend.services.grounding import ground
            from backend.services.pipeline import dispatch_to_pipeline

            evidence = ground(incident)

            from backend.domain.incident import NormalizedEvent

            ne_data = incident.normalized_event or {}
            ne = NormalizedEvent.model_validate(ne_data) if isinstance(ne_data, dict) else ne_data

            await repo.set_grounded(incident_id, ne, evidence, evidence.severity)
            # Reload the incident so the supervisor sees grounded status
            incident = await repo.get(incident_id) or incident
            await dispatch_to_pipeline(incident, repo=repo, supervisor=supervisor)
            await queue.ack(incident_id_str)
            logger.info("worker_grounded", incident_id=incident_id_str)

        except Exception as exc:
            logger.error(
                "worker_error",
                incident_id=incident_id_str,
                error=type(exc).__name__,
                detail=str(exc),
            )
            count = await repo.bump_attempt(incident_id)
            if count >= settings.ingest.max_attempts:
                await repo.mark_failed(incident_id, reason=type(exc).__name__)
                await queue.ack(incident_id_str)
                logger.warning("worker_failed_terminal", incident_id=incident_id_str)


async def _main_async() -> None:
    from backend.infra.cache import CacheProvider
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry, register_provider
    from backend.infra.db import register_db_provider
    from backend.infra.lifespan import sentinel_lifespan
    from backend.infra.observability import ObservabilityProvider
    from backend.infra.queue import QueueProvider
    from backend.infra.supervisor_provider import SupervisorProvider
    from backend.infra.vault import register_vault_provider

    settings = load_settings()

    clear_registry()
    register_vault_provider()
    register_db_provider()
    register_provider(ObservabilityProvider())
    register_provider(CacheProvider())
    register_provider(QueueProvider())
    register_provider(SupervisorProvider())

    from fastapi import FastAPI

    app = FastAPI()
    app.state.settings = settings

    async with sentinel_lifespan(app):
        container = app.state.container
        db_engine = container.db_engine
        queue = container.queue
        tracer = container.observability.tracer
        supervisor = container.supervisor

        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(db_engine.engine, expire_on_commit=False)
        async with session_factory() as session:
            from backend.repositories.incidents import IncidentRepository

            repo = IncidentRepository(session)
            await _run(settings, queue, repo, tracer, supervisor=supervisor)


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
