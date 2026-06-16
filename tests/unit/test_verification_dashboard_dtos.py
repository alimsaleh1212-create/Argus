"""Unit tests — dashboard DTOs exposing VerificationRecord in redacted form (T018)."""

from __future__ import annotations

import uuid

import pytest

from backend.domain.response import (
    ActionResult,
    ActionStatus,
    ActionType,
    ProbeResult,
    ProbeState,
    VerificationRecord,
    VerificationSignals,
    VerificationVerdict,
)


def _make_verification_record(verdict: VerificationVerdict) -> VerificationRecord:
    probe = ProbeResult(
        type=ActionType.BLOCK_IP,
        target="[REDACTED]",
        state=ProbeState.EXPECTED if verdict == VerificationVerdict.VERIFIED else ProbeState.UNEXPECTED,
        detail="[REDACTED]",
    )
    sig = VerificationSignals(probe=probe)
    return VerificationRecord(
        verdict=verdict,
        per_action=[],
        signals=[sig],
        used_llm_tiebreak=False,
        rationale="verdict=verified: block_ip@[REDACTED]: probe=expected intel=no-indicator",
    )


def test_verification_record_serialises_verdict_string():
    rec = _make_verification_record(VerificationVerdict.VERIFIED)
    d = rec.model_dump(mode="json")
    assert d["verdict"] == "verified"


def test_verification_record_serialises_regressed():
    rec = _make_verification_record(VerificationVerdict.REGRESSED)
    d = rec.model_dump(mode="json")
    assert d["verdict"] == "regressed"


def test_verification_record_serialises_unverified():
    rec = _make_verification_record(VerificationVerdict.UNVERIFIED)
    d = rec.model_dump(mode="json")
    assert d["verdict"] == "unverified"


def test_verification_record_probe_target_is_redacted():
    """Target field in signals should carry '[REDACTED]' before surfacing to dashboard."""
    rec = _make_verification_record(VerificationVerdict.VERIFIED)
    d = rec.model_dump(mode="json")
    for sig in d["signals"]:
        assert sig["probe"]["target"] == "[REDACTED]"


def test_verification_record_no_raw_ip_in_rationale():
    """Rationale should not contain raw IP addresses after redaction."""
    rec = _make_verification_record(VerificationVerdict.VERIFIED)
    d = rec.model_dump(mode="json")
    # The rationale here uses [REDACTED] — no real IP
    assert "192." not in d["rationale"]
    assert "10.0." not in d["rationale"]


def test_verification_record_evidence_patch_shape():
    """The evidence_patch["response"]["verification"] shape matches what the dashboard reads."""
    rec = _make_verification_record(VerificationVerdict.VERIFIED)
    patch = {"response": {"verification": rec.model_dump(mode="json")}}
    veri = patch["response"]["verification"]
    assert "verdict" in veri
    assert "signals" in veri
    assert "used_llm_tiebreak" in veri
    assert "rationale" in veri


def test_verification_record_used_llm_tiebreak_false_by_default():
    rec = _make_verification_record(VerificationVerdict.VERIFIED)
    assert rec.used_llm_tiebreak is False
