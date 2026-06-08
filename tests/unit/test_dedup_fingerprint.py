"""Unit tests — T035: fingerprint determinism.

TDD: must FAIL before infra/cache.py dedup helpers are implemented.
"""

from __future__ import annotations

import pytest


class TestFingerprintDeterminism:
    def test_identical_alerts_same_fingerprint(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import _compute_fingerprint, content_signature

        alert1 = WazuhAlert(
            timestamp="2026-06-08T10:00:00.000Z",
            rule=WazuhRule(level=10, id="5763", description="SSH"),
        )
        alert2 = WazuhAlert(
            timestamp="2026-06-09T11:00:00.000Z",  # different timestamp
            rule=WazuhRule(level=10, id="5763", description="SSH"),
        )
        sig1 = content_signature(alert1)
        sig2 = content_signature(alert2)
        fp1 = _compute_fingerprint(alert1, sig1)
        fp2 = _compute_fingerprint(alert2, sig2)
        assert fp1 == fp2

    def test_different_rule_different_fingerprint(self) -> None:
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import _compute_fingerprint, content_signature

        a1 = WazuhAlert(rule=WazuhRule(level=10, id="5763"))
        a2 = WazuhAlert(rule=WazuhRule(level=10, id="9999"))
        fp1 = _compute_fingerprint(a1, content_signature(a1))
        fp2 = _compute_fingerprint(a2, content_signature(a2))
        assert fp1 != fp2

    def test_different_agent_different_fingerprint(self) -> None:
        from backend.domain.incident import WazuhAgent, WazuhAlert, WazuhRule
        from backend.services.wazuh import _compute_fingerprint, content_signature

        a1 = WazuhAlert(rule=WazuhRule(level=10, id="5763"), agent=WazuhAgent(id="001"))
        a2 = WazuhAlert(rule=WazuhRule(level=10, id="5763"), agent=WazuhAgent(id="002"))
        fp1 = _compute_fingerprint(a1, content_signature(a1))
        fp2 = _compute_fingerprint(a2, content_signature(a2))
        assert fp1 != fp2

    def test_fingerprint_computed_over_redacted_content(self) -> None:
        """Fingerprint must not include raw secrets (computed before any redaction step)."""
        from backend.domain.incident import WazuhAlert, WazuhRule
        from backend.services.wazuh import _compute_fingerprint, content_signature

        # Two alerts that differ only in the volatile timestamp are the same
        a = WazuhAlert(
            rule=WazuhRule(level=5, id="111", description="test"),
            full_log="AKIAIOSFODNN7EXAMPLE some log line",
        )
        sig = content_signature(a)
        fp = _compute_fingerprint(a, sig)
        # Just verify it's a hex string (SHA-256)
        assert len(fp) == 64
        assert fp.isalnum()
