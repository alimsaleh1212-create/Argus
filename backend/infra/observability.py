"""Unified observability seam — logger + tracer + redactor as one lifespan singleton.

Provides:
  - Observability: the bundle consumers receive via Depends(get_obs).
  - ObservabilityProvider: builds the bundle once on startup, force-flushes
    spans on shutdown (FR-019); follows the Provider protocol from container.py.
  - Re-exports get_logger, bind_incident, clear_incident, span, record_llm_usage
    so consumers import from one place.

No-bypass rule (FR-018): all logging/tracing/redaction goes through this seam.
The no-bypass CI guard (T028) enforces that nothing in backend/ imports
opentelemetry, presidio, or logging directly outside of backend/infra/.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from backend.infra.logging import (
    bind_incident,
    clear_incident,
    get_logger,
)
from backend.infra.redaction import _CompositeRedactor, build_redactor
from backend.infra.tracing import _Tracer, build_tracer, record_llm_usage, span

if TYPE_CHECKING:
    from backend.infra.trace_repository import TraceRepository


logger = get_logger(__name__)


@dataclass
class Observability:
    """The unified observability bundle.

    Consumers receive this via Depends(get_obs); they call:
      - obs.get_logger(name) → structlog BoundLogger
      - obs.redactor.redact_text(text, boundary) → str
      - obs.redactor.redact_mapping(data, boundary) → dict
      - span(obs.tracer, ...) context manager
      - record_llm_usage(span, ...) helper
      - obs.bind_incident(id) / obs.clear_incident()
    """

    redactor: _CompositeRedactor
    tracer: _Tracer

    # Convenience re-exports so consumers only import from this module
    get_logger = staticmethod(get_logger)
    bind_incident = staticmethod(bind_incident)
    clear_incident = staticmethod(clear_incident)
    span = staticmethod(span)
    record_llm_usage = staticmethod(record_llm_usage)


class ObservabilityProvider:
    """Lifespan singleton provider for the unified observability seam.

    Registered after db_engine (the trace store depends on the DB engine).
    Builds Presidio engine + secret scrubber + OTel tracer once on startup.
    Force-flushes the span exporter on clean shutdown (FR-019).
    """

    name = "observability"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[Observability, None]:
        obs_settings = getattr(settings, "observability", None)

        presidio_enabled = getattr(obs_settings, "presidio_enabled", True)
        entropy_threshold = getattr(obs_settings, "entropy_threshold", 4.0)
        spacy_model = getattr(obs_settings, "spacy_model", "en_core_web_sm")
        max_attr_bytes = getattr(obs_settings, "span_attr_max_bytes", 8192)

        logger.info("observability_building")

        redactor = build_redactor(
            presidio_enabled=presidio_enabled,
            entropy_threshold=entropy_threshold,
            spacy_model=spacy_model,
        )

        # Trace repository requires the DB engine (registered before this provider)
        repo: TraceRepository | None = None
        try:
            from backend.infra.db import DbEngine

            db: DbEngine = getattr(settings, "_container", None) and getattr(
                settings._container, "db_engine", None
            )
            if db is not None:
                from backend.infra.trace_repository import TraceRepository as _TR

                repo = _TR(db.engine)
        except Exception:
            logger.warning("trace_repo_unavailable")

        tracer = build_tracer(exporter=repo, max_attr_bytes=max_attr_bytes)

        bundle = Observability(redactor=redactor, tracer=tracer)
        logger.info("observability_ready")

        # Periodic span flush: spans are enqueued synchronously off the incident
        # path; without a flush loop they only persist at shutdown, so traces
        # appear empty while incidents are in flight. Flush on a short cadence.
        flush_task: asyncio.Task[None] | None = None
        if repo is not None:
            flush_interval = float(getattr(obs_settings, "span_flush_interval_s", 2.0))

            async def _flush_loop() -> None:
                while True:
                    await asyncio.sleep(flush_interval)
                    try:
                        await repo.flush()
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        logger.warning("trace_periodic_flush_error", error=str(exc))

            flush_task = asyncio.create_task(_flush_loop(), name="trace-flush-loop")

        try:
            yield bundle
        finally:
            # Stop the flush loop, then force-flush any remainder (FR-019)
            if flush_task is not None:
                flush_task.cancel()
                try:
                    await flush_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            if repo is not None:
                try:
                    await repo.flush()
                    logger.info("trace_exporter_flushed")
                except Exception as exc:
                    logger.warning("trace_flush_failed", error=str(exc))
            logger.info("observability_disposed")
