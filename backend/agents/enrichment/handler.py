"""Enrichment stage orchestration — the StageHandler factory (DI by closure).

Read-only: no DB session, no action client — enrichment cannot write incident state. The closure
reduces to seven clear steps: build deterministic queries → gather context → one LLM call →
validate → map outcome → return StageResult (the supervisor merges evidence_patch, single writer).
"""

from __future__ import annotations

from backend.agents.enrichment.context import gather_context
from backend.agents.enrichment.queries import build_reference_query, extract_entities
from backend.agents.enrichment.reasoning import (
    build_request,
    decide_outcome,
    map_llm_error,
    report_from_response,
    split_tokens,
    tokens,
)
from backend.domain.corpus import CorpusRetriever
from backend.domain.incident import Incident
from backend.domain.llm import LlmError
from backend.domain.memory import MemoryStore
from backend.domain.pipeline import StageHandler, StageName, StageResult, ToolError


def make_enrichment_handler(
    llm: object,
    corpus: CorpusRetriever | None,
    memory: MemoryStore | None,
    intel: object | None,
    cfg: object,
) -> StageHandler:
    """Return a StageHandler closed over the read-only retrievers and config.

    No DB session, no action client — enrichment cannot write incident state.
    Any retriever may be None (best-effort: missing retriever → empty context).
    """
    corpus_k: int = getattr(cfg, "corpus_k", 5)
    memory_k: int = getattr(cfg, "memory_k", 5)
    max_indicators: int = getattr(cfg, "max_indicators", 5)
    consult_intel: bool = getattr(cfg, "consult_intel", True)

    async def run_enrichment(incident: Incident) -> StageResult:
        ev = incident.evidence or {}
        summary: str = ev.get("summary") or ""

        # 1. Build deterministic queries
        query = build_reference_query(ev)
        entities = extract_entities(ev, max_indicators)

        # 2. Fan-out retrieval concurrently + assemble the reasoning bundle
        external_raw, internal_raw = await gather_context(
            corpus=corpus,
            memory=memory,
            intel=intel,
            query=query,
            entities=entities,
            summary=summary,
            corpus_k=corpus_k,
            memory_k=memory_k,
            max_indicators=max_indicators,
            consult_intel=consult_intel,
        )

        # 3. One structured-output LLM call
        request = build_request(incident, external_raw, internal_raw, cfg)
        try:
            response = await llm.generate(  # type: ignore[union-attr]
                request, correlation_id=incident.correlation_id
            )
        except LlmError as exc:
            raise map_llm_error(exc) from exc
        except Exception as exc:
            raise ToolError(retryable=False, kind="llm_unexpected") from exc

        # 4. Validate — fail-closed on parse/validation failure
        try:
            report = report_from_response(response)
        except Exception as exc:
            raise ToolError(retryable=False, kind="malformed_output") from exc

        # 5. Map outcome
        outcome, disposition = decide_outcome(report, cfg)
        tokens_in, tokens_out, llm_model = split_tokens(response)
        note = (
            f"assessment={report.assessment} conf={report.confidence:.2f}: "
            f"{report.correlation_summary}"
        )[:200]

        # 6. Return StageResult — supervisor merges evidence_patch (single writer)
        return StageResult(
            stage=StageName.ENRICHMENT,
            outcome=outcome,
            tokens_consumed=tokens(response),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            llm_model=llm_model,
            disposition=disposition,
            evidence_patch={"enrichment": report.model_dump(mode="json")},
            note=note,
        )

    return run_enrichment
