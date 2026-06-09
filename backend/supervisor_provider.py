"""SupervisorProvider — builds the Supervisor singleton once at lifespan startup.

Mirrors QueueProvider / CacheProvider. Exposed as container.supervisor.
Lives at backend/ (not backend/infra/) so it can legally import from
backend.services and backend.agents without breaking the layered-architecture
import-linter contract.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any


class SupervisorProvider:
    name = "supervisor"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[Any, None]:
        from backend.agents.response import run_response
        from backend.domain.pipeline import StageName
        from backend.infra.tracing import build_tracer
        from backend.services.supervisor import Supervisor

        cfg = getattr(settings, "supervisor", None)
        if cfg is None:
            from backend.infra.config import SupervisorSettings
            cfg = SupervisorSettings()

        triage_cfg = getattr(settings, "triage", None)
        if triage_cfg is None:
            from backend.infra.config import TriageSettings
            triage_cfg = TriageSettings()

        enrichment_cfg = getattr(settings, "enrichment", None)
        if enrichment_cfg is None:
            from backend.infra.config import EnrichmentSettings
            enrichment_cfg = EnrichmentSettings()

        tracer_bundle = getattr(getattr(settings, "_container", None), "observability", None)
        tracer = tracer_bundle.tracer if tracer_bundle is not None else build_tracer(exporter=None)

        container = getattr(settings, "_container", None)
        llm_client = getattr(container, "llm", None)
        corpus_retriever = getattr(container, "corpus", None)
        intel_client = getattr(container, "intel", None)
        memory_store = getattr(container, "memory", None)

        if llm_client is not None:
            from backend.agents.triage import make_triage_handler
            triage_handler = make_triage_handler(llm_client, triage_cfg)
        else:
            # No LLM available — keep ADVANCE stub
            from backend.domain.incident import Incident
            from backend.domain.pipeline import StageOutcome, StageResult

            async def _stub_triage(incident: Incident) -> StageResult:
                return StageResult(
                    stage=StageName.TRIAGE, outcome=StageOutcome.ADVANCE, tokens_consumed=0
                )

            triage_handler = _stub_triage

        if llm_client is not None:
            from backend.agents.enrichment import make_enrichment_handler
            enrichment_handler = make_enrichment_handler(
                llm_client, corpus_retriever, memory_store, intel_client, enrichment_cfg
            )
        else:
            # No LLM available — keep ADVANCE stub
            from backend.domain.incident import Incident
            from backend.domain.pipeline import StageOutcome, StageResult

            async def _stub_enrichment(incident: Incident) -> StageResult:
                return StageResult(
                    stage=StageName.ENRICHMENT, outcome=StageOutcome.ADVANCE, tokens_consumed=0
                )

            enrichment_handler = _stub_enrichment

        stages = {
            StageName.TRIAGE: triage_handler,
            StageName.ENRICHMENT: enrichment_handler,
            StageName.RESPONSE: run_response,
        }

        supervisor = Supervisor(stages=stages, cfg=cfg, tracer=tracer)
        yield supervisor
