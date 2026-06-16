"""Verification tail (#15) — deterministic, read-only, fail-closed.

After any applied remediation, re-check each action's target via an executor probe + indicator
re-check (threat-intel + temporal-memory reputation fact) and aggregate a worst-case verdict.
No LLM on the common path. Every external call is wrapped fail-closed: any signal gap → UNVERIFIED,
never VERIFIED, and verification never raises into the caller's terminal-state decision.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping

from backend.agents.response._log import get_logger
from backend.domain.response import (
    ActionExecutor,
    ActionResult,
    ActionStatus,
    ActionType,
    IndicatorRecheck,
    ProbeResult,
    ProbeState,
    RemediationAction,
    VerificationRecord,
    VerificationSignals,
    VerificationVerdict,
    decide_verdict,
)

_logger = get_logger(__name__)


async def _safe_probe(executor: ActionExecutor, action: RemediationAction) -> ProbeResult:
    """Call probe(); any exception → INCONCLUSIVE (fail-closed, never raises)."""
    try:
        return await executor.probe(action)
    except Exception as exc:
        _logger.warning("verification_probe_error", action=action.type.value, error=str(exc))
        return ProbeResult(
            type=action.type,
            target=action.target,
            state=ProbeState.INCONCLUSIVE,
            detail="probe_error",
        )


async def _safe_intel_lookup(intel: object, target: str, kind: str) -> str:
    """Call intel.lookup(); any exception → 'unknown' (fail-closed, never raises)."""
    try:
        verdict = await intel.lookup(target, kind)  # type: ignore[union-attr]
        return str(getattr(verdict, "verdict", verdict) or "unknown")
    except Exception:
        return "unknown"


async def _safe_query_fact(memory: object, target: str) -> tuple[str | None, bool]:
    """Call memory.query_fact(entity, 'reputation', as_of=None); returns (value, is_current)."""
    try:
        state = await memory.query_fact(  # type: ignore[union-attr]
            {"name": target, "type": "indicator"}, "reputation", as_of=None
        )
        if state is None:
            return None, False
        return str(getattr(state, "value", None) or ""), getattr(state, "is_current", False)
    except Exception:
        return None, False


async def _check_action(
    result: ActionResult,
    action: RemediationAction,
    executors: Mapping[ActionType, ActionExecutor],
    intel: object | None,
    memory: object | None,
) -> VerificationSignals:
    """Probe one applied action + re-check its indicator (best-effort, concurrent)."""
    executor = executors.get(action.type)
    if executor is not None:
        probe_result = await _safe_probe(executor, action)
    else:
        probe_result = ProbeResult(
            type=action.type,
            target=action.target,
            state=ProbeState.INCONCLUSIVE,
            detail="no_executor",
        )

    recheck: IndicatorRecheck | None = None
    if intel is not None or memory is not None:
        intel_verdict = "unknown"
        fact_value: str | None = None
        fact_current = False

        tasks = []
        if intel is not None:
            tasks.append(_safe_intel_lookup(intel, action.target, "ip"))
        if memory is not None:
            tasks.append(_safe_query_fact(memory, action.target))

        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        idx = 0
        if intel is not None:
            v = gathered[idx]
            if not isinstance(v, Exception):
                intel_verdict = v  # type: ignore[assignment]
            idx += 1
        if memory is not None:
            m = gathered[idx]
            if not isinstance(m, Exception) and isinstance(m, tuple):
                fact_value, fact_current = m

        recheck = IndicatorRecheck(
            target=action.target,
            intel_verdict=intel_verdict,  # type: ignore[arg-type]
            fact_value=fact_value,
            fact_is_current=fact_current,
        )

    return VerificationSignals(probe=probe_result, recheck=recheck)


async def verify_remediation(
    *,
    applied_results: list[ActionResult],
    applied_actions: list[RemediationAction],
    executors: Mapping[ActionType, ActionExecutor],
    intel: object | None,
    memory: object | None,
    cfg: object,
) -> VerificationRecord:
    """Compute a verification verdict for all applied actions (deterministic, read-only).

    Best-effort + fail-closed: any signal gap → UNVERIFIED.
    Idempotency: callers must check evidence["response"]["verification"] before calling.
    """
    # Pair applied results with their actions (matched by type+target)
    pairs: list[tuple[ActionResult, RemediationAction]] = []
    for res in applied_results:
        if res.status == ActionStatus.APPLIED:
            action = next(
                (a for a in applied_actions if a.type == res.type and a.target == res.target),
                None,
            )
            if action is not None:
                pairs.append((res, action))

    if not pairs:
        # No applied actions — verdict is unverified (fail-closed)
        return VerificationRecord(
            verdict=VerificationVerdict.UNVERIFIED,
            per_action=list(applied_results),
            signals=[],
            rationale="No applied actions to verify.",
        )

    signals_list: list[VerificationSignals] = list(
        await asyncio.gather(*[_check_action(r, a, executors, intel, memory) for r, a in pairs])
    )

    verdict = decide_verdict(signals_list, cfg)

    rationale_parts = []
    for (_res, action), sig in zip(pairs, signals_list, strict=True):
        probe_desc = sig.probe.state.value
        intel_desc = sig.recheck.intel_verdict if sig.recheck else "no-indicator"
        rationale_parts.append(
            f"{action.type.value}@{action.target}: probe={probe_desc} intel={intel_desc}"
        )
    rationale = f"verdict={verdict.value}: " + "; ".join(rationale_parts)

    # Stamp per-action verification on results
    stamped: list[ActionResult] = []
    for (res, _action), sig in zip(pairs, signals_list, strict=True):
        per_action_verdict = decide_verdict([sig], cfg)
        stamped.append(res.model_copy(update={"verification": per_action_verdict}))

    return VerificationRecord(
        verdict=verdict,
        per_action=stamped,
        signals=signals_list,
        used_llm_tiebreak=False,
        rationale=rationale[:500],
    )
