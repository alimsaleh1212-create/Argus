"""Unit tests — ProbeState, ProbeResult, VerificationSignals, VerificationRecord models (T005).

Tests are written FIRST per Constitution II. They will fail (ImportError) until
T009–T011 implement the domain types.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.domain.response import (
    ActionType,
    IndicatorRecheck,
    ProbeResult,
    ProbeState,
    VerificationRecord,
    VerificationSignals,
    VerificationVerdict,
)

# ---------------------------------------------------------------------------
# ProbeResult
# ---------------------------------------------------------------------------


def test_probe_result_requires_type_target_state():
    r = ProbeResult(type=ActionType.BLOCK_IP, target="1.2.3.4", state=ProbeState.EXPECTED)
    assert r.state == ProbeState.EXPECTED
    assert r.detail == ""


def test_probe_result_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        ProbeResult(
            type=ActionType.BLOCK_IP,
            target="1.2.3.4",
            state=ProbeState.EXPECTED,
            unknown_field="x",
        )


def test_probe_result_all_states():
    for state in ProbeState:
        r = ProbeResult(type=ActionType.ISOLATE_HOST, target="host-1", state=state)
        assert r.state == state


# ---------------------------------------------------------------------------
# IndicatorRecheck
# ---------------------------------------------------------------------------


def test_indicator_recheck_defaults():
    r = IndicatorRecheck(target="10.0.0.1", intel_verdict="unknown")
    assert r.fact_value is None
    assert r.fact_is_current is False


def test_indicator_recheck_current_malicious():
    r = IndicatorRecheck(
        target="10.0.0.1",
        intel_verdict="malicious",
        fact_value="malicious",
        fact_is_current=True,
    )
    assert r.fact_is_current is True


def test_indicator_recheck_extra_forbidden():
    with pytest.raises(ValidationError):
        IndicatorRecheck(target="x", intel_verdict="benign", bogus=True)


# ---------------------------------------------------------------------------
# VerificationSignals
# ---------------------------------------------------------------------------


def test_verification_signals_no_recheck():
    probe = ProbeResult(type=ActionType.BLOCK_IP, target="1.2.3.4", state=ProbeState.INCONCLUSIVE)
    sig = VerificationSignals(probe=probe)
    assert sig.recheck is None


def test_verification_signals_frozen():
    probe = ProbeResult(type=ActionType.BLOCK_IP, target="1.2.3.4", state=ProbeState.EXPECTED)
    sig = VerificationSignals(probe=probe)
    with pytest.raises(ValidationError):
        sig.recheck = IndicatorRecheck(target="x", intel_verdict="benign")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# VerificationRecord
# ---------------------------------------------------------------------------


def test_verification_record_basic():
    probe = ProbeResult(type=ActionType.BLOCK_IP, target="1.2.3.4", state=ProbeState.EXPECTED)
    sig = VerificationSignals(probe=probe)
    rec = VerificationRecord(
        verdict=VerificationVerdict.VERIFIED,
        per_action=[],
        signals=[sig],
        rationale="Indicator re-checks clean; probe reports expected post-state.",
    )
    assert rec.used_llm_tiebreak is False
    assert rec.verdict == VerificationVerdict.VERIFIED


def test_verification_record_rationale_required():
    probe = ProbeResult(type=ActionType.BLOCK_IP, target="1.2.3.4", state=ProbeState.EXPECTED)
    sig = VerificationSignals(probe=probe)
    with pytest.raises(ValidationError):
        VerificationRecord(
            verdict=VerificationVerdict.VERIFIED,
            per_action=[],
            signals=[sig],
            rationale="",  # min_length=1
        )


def test_verification_record_extra_forbidden():
    probe = ProbeResult(type=ActionType.BLOCK_IP, target="1.2.3.4", state=ProbeState.EXPECTED)
    sig = VerificationSignals(probe=probe)
    with pytest.raises(ValidationError):
        VerificationRecord(
            verdict=VerificationVerdict.VERIFIED,
            per_action=[],
            signals=[sig],
            rationale="ok",
            unknown_field="x",
        )


def test_verification_record_serialises_to_dict():
    probe = ProbeResult(type=ActionType.BLOCK_IP, target="1.2.3.4", state=ProbeState.EXPECTED)
    sig = VerificationSignals(probe=probe)
    rec = VerificationRecord(
        verdict=VerificationVerdict.REGRESSED,
        per_action=[],
        signals=[sig],
        used_llm_tiebreak=False,
        rationale="Indicator still malicious.",
    )
    d = rec.model_dump(mode="json")
    assert d["verdict"] == "regressed"
    assert d["used_llm_tiebreak"] is False
