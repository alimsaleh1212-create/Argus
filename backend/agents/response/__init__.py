"""Response stage handler — playbook selection, auto-execution, HITL approval, verification tail.

Determinism-first: unambiguous catalog match → no LLM call. Only the ambiguous tail consults one
structured LlmClient call. Pure default-deny policy classifies actions: AUTO (allowlist) or
APPROVAL_REQUIRED. This is the ONLY stage injected with action executors (Constitution III).
Verification tail (#15): after any applied remediation, compute a deterministic verdict; no LLM on
the common path.

The implementation is split across the package modules for readability; this module re-exports the
stable public + test-facing surface so `backend.agents.response.<name>` keeps working:
  - catalog      — playbook types + loader
  - selection    — deterministic match / ambiguous-tail LLM / default-deny policy
  - execution    — per-action execute + audit
  - verification — read-only verdict computation
  - handler      — the StageHandler factory and its two passes
"""

from __future__ import annotations

from backend.agents.response.catalog import (
    PlaybookCatalog,
    PlaybookEntry,
    load_playbook_catalog,
)
from backend.agents.response.execution import _execute_with_audit
from backend.agents.response.handler import (
    _append_verification_audit,
    _finalize_with_verification,
    _pass_a,
    _pass_b,
    make_response_handler,
    run_response,
)
from backend.agents.response.selection import (
    PLAYBOOK_SELECT_SCHEMA,
    _build_actions,
    _criteria_match,
    _map_and_raise_llm_error,
    _tokens,
    classify,
    select_playbook,
)
from backend.agents.response.verification import (
    _safe_intel_lookup,
    _safe_probe,
    _safe_query_fact,
    verify_remediation,
)

# Re-exported surface. Public API + the internal names tests import directly
# (`backend.agents.response.<name>`) — listed here so they survive the package split.
__all__ = [
    "PLAYBOOK_SELECT_SCHEMA",
    "PlaybookCatalog",
    "PlaybookEntry",
    "_append_verification_audit",
    "_build_actions",
    "_criteria_match",
    "_execute_with_audit",
    "_finalize_with_verification",
    "_map_and_raise_llm_error",
    "_pass_a",
    "_pass_b",
    "_safe_intel_lookup",
    "_safe_probe",
    "_safe_query_fact",
    "_tokens",
    "classify",
    "load_playbook_catalog",
    "make_response_handler",
    "run_response",
    "select_playbook",
    "verify_remediation",
]
