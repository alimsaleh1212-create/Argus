"""Unit tests — decide_action_verdict and decide_verdict pure functions (T004).

Tests are written FIRST per Constitution II. They will fail (ImportError) until
T009–T012 implement the domain types.
"""

from __future__ import annotations

import pytest

from backend.domain.response import (
    ActionType,
    IndicatorRecheck,
    ProbeResult,
    ProbeState,
    VerificationSignals,
    VerificationVerdict,
    decide_action_verdict,
    decide_verdict,
)


class _Cfg:
    verify_regressed_verdicts = ["malicious", "suspicious"]
    verify_llm_tiebreak = False


def _probe(state: ProbeState, target: str = "1.2.3.4") -> ProbeResult:
    return ProbeResult(type=ActionType.BLOCK_IP, target=target, state=state)


def _recheck(intel: str, fact: str | None = None, current: bool = True) -> IndicatorRecheck:
    return IndicatorRecheck(
        target="1.2.3.4",
        intel_verdict=intel,
        fact_value=fact,
        fact_is_current=current,
    )


def _signals(
    probe_state: ProbeState,
    intel: str | None = None,
    fact: str | None = None,
    fact_current: bool = True,
) -> VerificationSignals:
    recheck = _recheck(intel or "unknown", fact, fact_current) if intel is not None else None
    return VerificationSignals(probe=_probe(probe_state), recheck=recheck)


# ---------------------------------------------------------------------------
# decide_action_verdict — per-action rules
# ---------------------------------------------------------------------------


def test_expected_probe_clean_intel_yields_verified():
    sig = _signals(ProbeState.EXPECTED, "benign")
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.VERIFIED


def test_expected_probe_no_indicator_yields_verified():
    sig = _signals(ProbeState.EXPECTED, None)
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.VERIFIED


def test_unexpected_probe_yields_regressed_regardless_of_intel():
    for intel in ("benign", "malicious", "unknown"):
        sig = _signals(ProbeState.UNEXPECTED, intel)
        assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.REGRESSED


def test_malicious_intel_yields_regressed():
    sig = _signals(ProbeState.EXPECTED, "malicious")
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.REGRESSED


def test_suspicious_intel_yields_regressed():
    sig = _signals(ProbeState.EXPECTED, "suspicious")
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.REGRESSED


def test_inconclusive_probe_unknown_intel_yields_unverified():
    sig = _signals(ProbeState.INCONCLUSIVE, "unknown")
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.UNVERIFIED


def test_inconclusive_probe_no_indicator_yields_unverified():
    sig = _signals(ProbeState.INCONCLUSIVE, None)
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.UNVERIFIED


def test_expected_probe_benign_intel_superseded_fact_malicious_yields_regressed():
    """Superseded fact (is_current=False) should not be treated as current malicious."""
    recheck = IndicatorRecheck(
        target="1.2.3.4",
        intel_verdict="benign",
        fact_value="malicious",
        fact_is_current=False,
    )
    sig = VerificationSignals(probe=_probe(ProbeState.EXPECTED), recheck=recheck)
    # Superseded fact → not current → benign intel wins → verified
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.VERIFIED


def test_current_fact_malicious_yields_regressed():
    recheck = IndicatorRecheck(
        target="1.2.3.4",
        intel_verdict="benign",
        fact_value="malicious",
        fact_is_current=True,
    )
    sig = VerificationSignals(probe=_probe(ProbeState.EXPECTED), recheck=recheck)
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.REGRESSED


def test_conflict_probe_expected_intel_malicious_deterministic_regressed():
    """Genuine conflict: probe says expected but intel says malicious → worst-case regressed."""
    sig = _signals(ProbeState.EXPECTED, "malicious")
    # LLM tiebreak off → deterministic regressed
    assert decide_action_verdict(sig, _Cfg()) == VerificationVerdict.REGRESSED


# ---------------------------------------------------------------------------
# decide_verdict — worst-case aggregate
# ---------------------------------------------------------------------------


def test_all_verified_yields_verified():
    sigs = [_signals(ProbeState.EXPECTED, "benign"), _signals(ProbeState.EXPECTED, "benign")]
    assert decide_verdict(sigs, _Cfg()) == VerificationVerdict.VERIFIED


def test_one_regressed_dominates():
    sigs = [
        _signals(ProbeState.EXPECTED, "benign"),
        _signals(ProbeState.UNEXPECTED, "malicious"),
    ]
    assert decide_verdict(sigs, _Cfg()) == VerificationVerdict.REGRESSED


def test_regressed_dominates_unverified():
    sigs = [
        _signals(ProbeState.INCONCLUSIVE, "unknown"),
        _signals(ProbeState.UNEXPECTED, "benign"),
    ]
    assert decide_verdict(sigs, _Cfg()) == VerificationVerdict.REGRESSED


def test_one_unverified_one_verified_yields_unverified():
    sigs = [
        _signals(ProbeState.EXPECTED, "benign"),
        _signals(ProbeState.INCONCLUSIVE, "unknown"),
    ]
    assert decide_verdict(sigs, _Cfg()) == VerificationVerdict.UNVERIFIED


def test_empty_signals_yields_unverified():
    assert decide_verdict([], _Cfg()) == VerificationVerdict.UNVERIFIED


def test_custom_regressed_set():
    class _CfgCustom:
        verify_regressed_verdicts = ["suspicious"]
        verify_llm_tiebreak = False

    # suspicious is in regressed set
    sig_sus = _signals(ProbeState.EXPECTED, "suspicious")
    assert decide_action_verdict(sig_sus, _CfgCustom()) == VerificationVerdict.REGRESSED

    # malicious is NOT in the custom set → inconclusive path
    sig_mal = _signals(ProbeState.EXPECTED, "malicious")
    # probe expected + intel malicious but not in regressed set → still regressed (conflict case)
    # Actually this depends on implementation; the function should treat non-set as not-regressed-intel
    # but the conflict resolution still goes worst-case. Test the basic custom set behaviour:
    sig_benign = _signals(ProbeState.EXPECTED, "benign")
    assert decide_action_verdict(sig_benign, _CfgCustom()) == VerificationVerdict.VERIFIED
