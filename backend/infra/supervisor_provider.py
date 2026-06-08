"""SupervisorProvider — builds the Supervisor singleton once at lifespan startup.

Mirrors QueueProvider / CacheProvider. Exposed as container.supervisor.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any


class SupervisorProvider:
    name = "supervisor"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[Any, None]:
        from backend.agents.enrichment import run_enrichment
        from backend.agents.response import run_response
        from backend.agents.triage import run_triage
        from backend.domain.pipeline import StageName
        from backend.infra.tracing import build_tracer
        from backend.services.supervisor import Supervisor

        cfg = getattr(settings, "supervisor", None)
        if cfg is None:
            from backend.infra.config import SupervisorSettings
            cfg = SupervisorSettings()

        tracer_bundle = getattr(getattr(settings, "_container", None), "observability", None)
        tracer = tracer_bundle.tracer if tracer_bundle is not None else build_tracer(exporter=None)

        stages = {
            StageName.TRIAGE: run_triage,
            StageName.ENRICHMENT: run_enrichment,
            StageName.RESPONSE: run_response,
        }

        supervisor = Supervisor(stages=stages, cfg=cfg, tracer=tracer)
        yield supervisor
