"""Gate runner registry.

GATE_REGISTRY maps gate name → async callable:
    async (spec: GateSpec, provider: str | None) -> GateResult

validate_registry() checks declared (yaml) ⇔ registered (code).
Orphan gate (declared, no runner) or stale runner (registered, no spec)
→ raises RegistryMismatchError before scoring (exit 2 from CLI).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.domain.eval import GateResult, GateSpec

Runner = Callable[["GateSpec", str | None], Awaitable["GateResult"]]

GATE_REGISTRY: dict[str, Runner] = {}


class RegistryMismatchError(RuntimeError):
    """Raised when declared gates and registered runners don't match."""


def validate_registry(specs: list[GateSpec]) -> None:
    declared = {s.name for s in specs}
    registered = set(GATE_REGISTRY)

    orphans = declared - registered
    stale = registered - declared

    msgs: list[str] = []
    if orphans:
        msgs.append(f"orphan gates (declared in yaml, no runner): {sorted(orphans)}")
    if stale:
        msgs.append(f"stale runners (registered, not in yaml): {sorted(stale)}")
    if msgs:
        raise RegistryMismatchError("; ".join(msgs))
