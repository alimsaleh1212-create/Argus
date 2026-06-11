"""Async worker — consumes the incident queue and runs grounding.

Entrypoint: python -m backend.worker
Same image as the API (one image, two containers).
"""

from __future__ import annotations

import asyncio
import uuid

from backend.infra.logging import get_logger

logger = get_logger(__name__)


async def _run(settings, queue, repo, tracer, supervisor=None, memory=None) -> None:
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

            # Best-effort memory write: off the disposition path, never blocks/raises
            if memory is not None:
                _maybe_record_episode(incident_id, incident_id_str, repo, memory, settings)

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


def _maybe_record_episode(incident_id, incident_id_str, repo, memory, settings):
    """Schedule best-effort episode write as a fire-and-forget asyncio task."""
    import asyncio

    async def _do_record():
        try:
            from backend.domain.incident import IncidentStatus
            from backend.infra.redaction import build_redactor
            from backend.services.memory import record_episode

            incident = await repo.get(incident_id)
            if incident is None:
                return
            # Only write for terminal incidents
            terminal = {IncidentStatus.RESOLVED, IncidentStatus.ESCALATED, IncidentStatus.FAILED}
            if incident.status not in terminal:
                return

            obs_settings = settings.observability
            redactor = build_redactor(presidio_enabled=obs_settings.presidio_enabled)
            await record_episode(incident, memory, redactor)
            logger.info("memory_episode_recorded", incident_id=incident_id_str)
        except Exception as exc:
            logger.warning(
                "memory_episode_error",
                incident_id=incident_id_str,
                error=str(exc),
            )

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_do_record())
    except Exception as exc:
        logger.warning("memory_schedule_error", error=str(exc))


async def _sweep_expired_approvals(settings, db_engine, supervisor) -> None:
    """Periodic timeout sweeper: expire past-deadline pending approvals (RD7).

    Spawned as a background task alongside _run. Off the synchronous path.
    """
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker

    response_cfg = getattr(settings, "response", None)
    sweep_interval_s: int = getattr(response_cfg, "sweep_interval_s", 60) if response_cfg else 60

    session_factory = async_sessionmaker(db_engine.engine, expire_on_commit=False)

    logger.info("approval_sweeper_started", interval_s=sweep_interval_s)
    while True:
        try:
            await asyncio.sleep(sweep_interval_s)
            now = datetime.now(UTC).replace(tzinfo=None)
            async with session_factory() as session:
                from backend.repositories.approvals import ApprovalRepository
                from backend.repositories.audit import AuditRepository
                from backend.repositories.incidents import IncidentRepository

                approval_repo = ApprovalRepository(session)
                audit_repo = AuditRepository(session)
                incident_repo = IncidentRepository(session)

                expired = await approval_repo.list_pending_expired(now)
                for record in expired:
                    from backend.domain.response import ApprovalStatus
                    resolved = await approval_repo.resolve(
                        record.id,
                        to=ApprovalStatus.EXPIRED,
                        decided_by="timeout",
                    )
                    if resolved:
                        await supervisor.expire_incident(
                            record.incident_id, incident_repo, audit_repo=audit_repo
                        )
                        logger.info(
                            "approval_sweeper_expired",
                            approval_id=record.id,
                            incident_id=str(record.incident_id),
                        )
        except asyncio.CancelledError:
            logger.info("approval_sweeper_stopped")
            break
        except Exception as exc:
            logger.warning("approval_sweeper_error", error=str(exc))


async def _main_async() -> None:
    from backend.infra.cache import CacheProvider
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry, register_provider
    from backend.infra.corpus import CorpusProvider
    from backend.infra.db import register_db_provider
    from backend.infra.intel import IntelProvider
    from backend.infra.lifespan import sentinel_lifespan
    from backend.infra.llm import register_llm_provider
    from backend.infra.memory import MemoryProvider
    from backend.infra.observability import ObservabilityProvider
    from backend.infra.queue import QueueProvider
    from backend.infra.vault import register_vault_provider
    from backend.supervisor_provider import SupervisorProvider

    settings = load_settings()

    clear_registry()
    register_vault_provider()
    register_db_provider()
    register_provider(ObservabilityProvider())
    register_provider(CacheProvider())
    register_provider(QueueProvider())
    register_llm_provider()  # must be before SupervisorProvider so container.llm exists
    # memory/corpus/intel must be registered before SupervisorProvider so the
    # enrichment handler closure can read them from the container at build time
    register_provider(MemoryProvider())
    register_provider(CorpusProvider())
    register_provider(IntelProvider())
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
        memory = getattr(container, "memory", None)

        from sqlalchemy.ext.asyncio import async_sessionmaker

        session_factory = async_sessionmaker(db_engine.engine, expire_on_commit=False)

        # Spawn the approval-timeout sweeper alongside the main loop (RD7)
        sweeper_task = asyncio.create_task(
            _sweep_expired_approvals(settings, db_engine, supervisor)
        )

        try:
            async with session_factory() as session:
                from backend.repositories.incidents import IncidentRepository

                repo = IncidentRepository(session)
                await _run(settings, queue, repo, tracer, supervisor=supervisor, memory=memory)
        finally:
            sweeper_task.cancel()
            try:
                await sweeper_task
            except asyncio.CancelledError:
                pass


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
