"""Async worker — consumes the incident queue and runs grounding.

Entrypoint: python -m backend.worker
Same image as the API (one image, two containers).
"""

from __future__ import annotations

import asyncio
import uuid

from backend.infra.logging import get_logger
from backend.repositories.incidents import IncidentRepository

logger = get_logger(__name__)


async def _apply_feedback_bias(
    evidence,
    incident,
    memory,
    settings,
) -> object:
    """At the grounded boundary: look up prior outcomes and bias severity/flags.

    Pure bias rules are in domain/feedback.py; this function is the I/O seam.
    Any error is swallowed and returns the original evidence (fail-open).
    """
    from backend.domain.incident import NormalizedEvent
    from backend.domain.redaction import Boundary
    from backend.infra.redaction import build_redactor
    from backend.services.feedback import gather_feedback
    from backend.services.memory import _extract_entities

    feedback_cfg = getattr(settings, "feedback", None)
    if feedback_cfg is None or not getattr(feedback_cfg, "enabled", True):
        return evidence
    if memory is None:
        return evidence

    try:
        ne_data = incident.normalized_event or {}
        ne = NormalizedEvent.model_validate(ne_data) if isinstance(ne_data, dict) else ne_data

        obs_settings = settings.observability
        redactor = build_redactor(presidio_enabled=obs_settings.presidio_enabled)
        entities = _extract_entities(ne, redactor)

        signals = await gather_feedback(
            memory=memory,
            entities=entities,
            cfg=feedback_cfg,
        )
        if not signals:
            return evidence

        from backend.domain.feedback import (
            decide_severity_bias,
            has_prior_failure,
        )

        original_severity = evidence.severity
        biased_severity = decide_severity_bias(evidence.severity, signals, feedback_cfg)
        flags = list(evidence.flags)
        prior_failure_added = has_prior_failure(signals, feedback_cfg)
        if prior_failure_added:
            flags.append("prior_failure")

        # Memory-influenced decision = severity bumped or prior-failure flag attached.
        bias_applied = biased_severity != original_severity or prior_failure_added

        # Redact indicator values before the operational evidence slice.
        redacted_signals = []
        for s in signals:
            redacted_indicator = redactor.redact_text(s.indicator, Boundary.OPERATIONAL)
            redacted_signals.append(
                {
                    "indicator": redacted_indicator,
                    "outcome": s.outcome.value,
                    "is_current": s.is_current,
                    "observed_at": (
                        s.observed_at.isoformat() if s.observed_at is not None else None
                    ),
                }
            )

        prior_outcome = {
            "signals": redacted_signals,
            "biased_severity": biased_severity.value,
        }

        # Evidence is a Pydantic model; build a patched dict.
        evidence_patch = evidence.model_copy(
            update={
                "severity": biased_severity,
                "flags": flags,
            }
        )
        evidence_dict = evidence_patch.model_dump(mode="json")
        evidence_dict["prior_outcome"] = prior_outcome
        evidence_dict["feedback"] = {"bias_applied": bias_applied}
        return evidence.__class__.model_validate(evidence_dict)
    except Exception as exc:
        logger.warning("feedback_bias_error", incident_id=str(incident.id), error=str(exc))
        return evidence


async def _run(
    settings, queue, repo, tracer, supervisor=None, memory=None, session_factory=None
) -> None:
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
            evidence = await _apply_feedback_bias(evidence, incident, memory, settings)

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
            if memory is not None and session_factory is not None:
                _maybe_record_episode(
                    incident_id, incident_id_str, session_factory, memory, settings
                )

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


async def _record_episode_isolated(incident_id, incident_id_str, session_factory, memory, settings):
    """Off-path episode write using its OWN session (never the main loop's).

    Best-effort: any error is logged and swallowed, never raised.
    """
    try:
        from backend.domain.incident import IncidentStatus
        from backend.infra.redaction import build_redactor
        from backend.services.memory import record_episode, record_outcome_facts

        async with session_factory() as session:
            repo = IncidentRepository(session)
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

            feedback_cfg = getattr(settings, "feedback", None)
            if feedback_cfg is not None and getattr(feedback_cfg, "enabled", True):
                await record_outcome_facts(incident, memory, redactor, cfg=feedback_cfg)
                logger.info("memory_outcome_facts_recorded", incident_id=incident_id_str)
    except Exception as exc:
        logger.warning("memory_episode_error", incident_id=incident_id_str, error=str(exc))


def _maybe_record_episode(incident_id, incident_id_str, session_factory, memory, settings):
    """Schedule the isolated episode write as a fire-and-forget task."""
    import asyncio

    try:
        loop = asyncio.get_event_loop()
        loop.create_task(
            _record_episode_isolated(
                incident_id, incident_id_str, session_factory, memory, settings
            )
        )
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
    from backend.corpus_provider import CorpusProvider
    from backend.infra.cache import CacheProvider
    from backend.infra.config import load_settings
    from backend.infra.container import clear_registry, register_provider
    from backend.infra.db import register_db_provider
    from backend.infra.intel import IntelProvider
    from backend.infra.lifespan import argus_lifespan
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

    async with argus_lifespan(app):
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
                repo = IncidentRepository(session)
                await _run(
                    settings,
                    queue,
                    repo,
                    tracer,
                    supervisor=supervisor,
                    memory=memory,
                    session_factory=session_factory,
                )
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
