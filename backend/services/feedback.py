"""Feedback consumption service — read-only, deterministic, fail-open (#16).

``gather_feedback`` mirrors the enrichment `_safe(...)` + `gather` pattern:
concurrent `query_fact` lookups bounded by `feedback.max_indicators`, with any
error degrading to empty context rather than blocking disposition.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from backend.domain.feedback import FeedbackSignal, RemediationOutcome
from backend.domain.memory import EntityRef, FactState

if TYPE_CHECKING:
    from backend.infra.config import FeedbackSettings


async def _safe(coro: Any, fallback: Any) -> Any:
    """Run a coroutine, returning fallback on any exception (degraded context)."""
    try:
        return await coro
    except Exception:
        return fallback


async def gather_feedback(
    *,
    memory: Any,
    entities: list[EntityRef],
    cfg: FeedbackSettings,
) -> list[FeedbackSignal]:
    """Return current failure-class (or verified) FeedbackSignals for the given entities.

    - Reads `query_fact(entity, cfg.outcome_fact_type, as_of=None)` for each entity.
    - Bounded by `cfg.max_indicators`.
    - Drops superseded facts (`is_current=False`).
    - Memory outage / error → `[]` (no bias; baseline v1 behavior).

    Read-key MUST equal write-key: callers must pass the same EntityRef values
    that `record_outcome_facts` wrote.
    """
    if memory is None or not getattr(cfg, "enabled", True):
        return []

    fact_type: str = getattr(cfg, "outcome_fact_type", "remediation_outcome")
    max_indicators: int = getattr(cfg, "max_indicators", 5)

    targets = entities[:max_indicators]
    if not targets:
        return []

    tasks = [
        _safe(memory.query_fact(entity, fact_type, as_of=None), FactState())
        for entity in targets
    ]
    results = await asyncio.gather(*tasks)

    signals: list[FeedbackSignal] = []
    for entity, state in zip(targets, results, strict=False):
        if not isinstance(state, FactState):
            continue
        if not state.is_current or state.fact is None:
            continue
        try:
            outcome = RemediationOutcome(state.fact.value)
        except ValueError:
            continue
        signals.append(
            FeedbackSignal(
                indicator=entity.value,
                outcome=outcome,
                is_current=True,
                observed_at=state.fact.valid_from,
            )
        )

    return signals
