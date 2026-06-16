"""Redaction eval gate helper — SPEC-remediation-verification #15 (T024).

Extends the redaction gate with seeded-secret coverage for:
  - VerificationRecord target / detail fields (planted credential patterns)
  - Dashboard verdict view paths (evidence JSON key-value rendering)

`_run_redaction_scenarios()` is called by run_redaction in deterministic.py.
Returns (cred_leaks, pii_leaks) where each represents count of unredacted secrets.

Uses build_redactor(presidio_enabled=False) — avoids loading the spaCy/Presidio
model in CI (entropy + explicit regex patterns cover credential patterns).
"""

from __future__ import annotations

import json

import pytest

PLANTED_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
PLANTED_BEARER = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
PLANTED_KV_SECRET = "api_key=sk-prod-ABCDEF1234567890abcdef"

# ---------------------------------------------------------------------------
# _run_redaction_scenarios — harness callable
# ---------------------------------------------------------------------------


async def _run_redaction_scenarios() -> tuple[int, int]:
    """Run verification-specific redaction scenarios.

    Returns (cred_leaks, pii_leaks).  Each scenario plants a known secret value
    in a verification record field, applies the redactor, and checks that the
    raw secret is absent from the output.
    """
    from backend.domain.redaction import Boundary
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    cred_leaks = 0

    # --- Scenario 1: AWS key in ProbeResult.detail ---
    evidence_1 = {
        "response": {
            "verification": {
                "verdict": "unverified",
                "per_action": [
                    {
                        "probe": {
                            "type": "block_ip",
                            "target": "1.2.3.4",
                            "state": "inconclusive",
                            "detail": f"probe timeout; probe_token={PLANTED_AWS_KEY}",
                        },
                        "recheck": None,
                    }
                ],
                "signals": [],
                "used_llm_tiebreak": False,
                "rationale": "Probe timed out during verification.",
            }
        }
    }
    redacted_1 = redactor.redact_mapping(evidence_1, Boundary.OPERATIONAL)
    redacted_str_1 = json.dumps(redacted_1)
    if PLANTED_AWS_KEY in redacted_str_1:
        cred_leaks += 1

    # --- Scenario 2: Bearer token in ProbeResult.detail ---
    evidence_2 = {
        "response": {
            "verification": {
                "verdict": "unverified",
                "per_action": [
                    {
                        "probe": {
                            "type": "revoke_token",
                            "target": "service-account",
                            "state": "inconclusive",
                            "detail": f"upstream error: auth header was {PLANTED_BEARER}",
                        },
                        "recheck": None,
                    }
                ],
                "signals": [],
                "used_llm_tiebreak": False,
                "rationale": "Token revocation probe returned an auth error.",
            }
        }
    }
    redacted_2 = redactor.redact_mapping(evidence_2, Boundary.OPERATIONAL)
    redacted_str_2 = json.dumps(redacted_2)
    # Extract only the JWT part for the check (the Bearer prefix may remain)
    jwt_part = PLANTED_BEARER.split(" ", 1)[-1]
    if jwt_part in redacted_str_2:
        cred_leaks += 1

    # --- Scenario 3: kv-style credential in VerificationRecord.rationale ---
    evidence_3 = {
        "response": {
            "verification": {
                "verdict": "unverified",
                "per_action": [],
                "signals": [],
                "used_llm_tiebreak": False,
                "rationale": f"Probe response included credential: {PLANTED_KV_SECRET}",
            }
        }
    }
    redacted_3 = redactor.redact_mapping(evidence_3, Boundary.OPERATIONAL)
    redacted_str_3 = json.dumps(redacted_3)
    # The kv pattern scrubs the value part after api_key=
    kv_value = PLANTED_KV_SECRET.split("=", 1)[-1]
    if kv_value in redacted_str_3:
        cred_leaks += 1

    # --- Scenario 4: AWS key surfaced in dashboard-view evidence path ---
    dashboard_payload = {
        "incident_id": "abc123",
        "disposition": "remediation_unverified",
        "evidence": {
            "verification": {
                "verdict": "unverified",
                "rationale": f"Error details: credential={PLANTED_AWS_KEY}",
            }
        },
    }
    redacted_4 = redactor.redact_mapping(dashboard_payload, Boundary.OPERATIONAL)
    redacted_str_4 = json.dumps(redacted_4)
    if PLANTED_AWS_KEY in redacted_str_4:
        cred_leaks += 1

    return cred_leaks, 0  # pii_leaks handled by presidio (disabled here — always 0)


# ---------------------------------------------------------------------------
# pytest tests — same scenarios run as individual test functions
# ---------------------------------------------------------------------------


def test_aws_key_in_probe_detail_is_redacted():
    """AWS key in ProbeResult.detail must not survive redact_mapping."""
    import asyncio

    from backend.domain.redaction import Boundary
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    evidence = {
        "response": {
            "verification": {
                "verdict": "unverified",
                "per_action": [
                    {
                        "probe": {
                            "type": "block_ip",
                            "target": "1.2.3.4",
                            "state": "inconclusive",
                            "detail": f"probe timeout; probe_token={PLANTED_AWS_KEY}",
                        }
                    }
                ],
                "rationale": "Inconclusive.",
            }
        }
    }
    out = redactor.redact_mapping(evidence, Boundary.OPERATIONAL)
    assert PLANTED_AWS_KEY not in json.dumps(out), (
        f"AWS key found unredacted in verification evidence: {json.dumps(out)[:200]}"
    )


def test_bearer_token_in_probe_detail_is_redacted():
    """Bearer JWT in ProbeResult.detail must be scrubbed."""
    from backend.domain.redaction import Boundary
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    evidence = {
        "response": {
            "verification": {
                "verdict": "unverified",
                "per_action": [
                    {
                        "probe": {
                            "type": "revoke_token",
                            "target": "svc-account",
                            "state": "inconclusive",
                            "detail": f"upstream error: auth was {PLANTED_BEARER}",
                        }
                    }
                ],
                "rationale": "Token revocation inconclusive.",
            }
        }
    }
    out = redactor.redact_mapping(evidence, Boundary.OPERATIONAL)
    jwt_part = PLANTED_BEARER.split(" ", 1)[-1]
    assert jwt_part not in json.dumps(out), (
        f"Bearer JWT found unredacted in verification evidence"
    )


def test_kv_secret_in_rationale_is_redacted():
    """api_key=<value> pattern in VerificationRecord.rationale must be scrubbed."""
    from backend.domain.redaction import Boundary
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    evidence = {
        "response": {
            "verification": {
                "verdict": "unverified",
                "per_action": [],
                "rationale": f"Probe included credential: {PLANTED_KV_SECRET}",
            }
        }
    }
    out = redactor.redact_mapping(evidence, Boundary.OPERATIONAL)
    kv_value = PLANTED_KV_SECRET.split("=", 1)[-1]
    assert kv_value not in json.dumps(out), (
        f"KV secret value found unredacted in verification rationale"
    )


def test_aws_key_in_dashboard_evidence_path_is_redacted():
    """AWS key in dashboard incident evidence (verdict view) must not leak."""
    from backend.domain.redaction import Boundary
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    payload = {
        "incident_id": "abc123",
        "disposition": "remediation_unverified",
        "evidence": {
            "verification": {
                "verdict": "unverified",
                "rationale": f"Error details: credential={PLANTED_AWS_KEY}",
            }
        },
    }
    out = redactor.redact_mapping(payload, Boundary.OPERATIONAL)
    assert PLANTED_AWS_KEY not in json.dumps(out), (
        f"AWS key found unredacted in dashboard evidence path"
    )


def test_remediation_unverified_disposition_survives_redaction():
    """The disposition string 'remediation_unverified' must not be redacted."""
    from backend.domain.redaction import Boundary
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    payload = {"disposition": "remediation_unverified", "verdict": "unverified"}
    out = redactor.redact_mapping(payload, Boundary.OPERATIONAL)
    assert out["disposition"] == "remediation_unverified", (
        f"disposition was unexpectedly altered: {out['disposition']}"
    )
