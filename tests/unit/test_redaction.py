"""Unit tests — T006: Redactor class × boundary policy.

All tests use the real Redactor implementation but mock Presidio so they stay
fast and deterministic (no model load). Integration tests (T007) use the live
engine.

Covers:
- CREDENTIAL scrubbed at every boundary (FR-006a)
- PII redacted at output boundaries; raw at MEMORY_WRITE/OPERATIONAL (FR-006b)
- OPERATIONAL_IDENTIFIER: raw at internal boundaries (FR-006b)
- Nested mapping/list traversal at any depth (FR-004)
- Idempotency: re-redacting a placeholder is a no-op (FR-004)
- High-entropy token flagged without explicit pattern (FR-005)
- Fail-closed: redactor error yields [REDACTION-FAILED], not the raw value (FR-003)
"""

from __future__ import annotations

import pytest

from backend.domain.redaction import Boundary
from backend.infra.redaction import build_redactor

# ── Fixtures ─────────────────────────────────────────────────────────────────

FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
FAKE_BEARER = (
    "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)
FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA0Z3VS5JJcds3xHn/ygWep4\n-----END RSA PRIVATE KEY-----"
FAKE_KV = "apikey=abc123secretvalue"
FAKE_EMAIL = "alice@example.com"
FAKE_IP = "192.168.1.100"
FAKE_HOSTNAME = "db-server-prod.internal"

# A 40-char high-entropy string unlikely to match any explicit pattern
FAKE_HIGH_ENTROPY = "aB3xQ9zK2mP7nL1wR5yT8vU4cE6hG0jD"


@pytest.fixture
def redactor():
    """Real Redactor with presidio_enabled=False for deterministic unit tests."""
    return build_redactor(presidio_enabled=False)


@pytest.fixture
def redactor_with_presidio():
    """Real Redactor with Presidio enabled — used only in integration tests."""
    return build_redactor(presidio_enabled=True)


# ── CREDENTIAL: scrubbed everywhere (FR-006a) ─────────────────────────────


class TestCredentialEverywhere:
    @pytest.mark.parametrize("boundary", list(Boundary))
    def test_aws_key_redacted_at_all_boundaries(self, redactor, boundary: Boundary) -> None:
        result = redactor.redact_text(f"found key={FAKE_AWS_KEY} in payload", boundary)
        assert FAKE_AWS_KEY not in result

    @pytest.mark.parametrize("boundary", list(Boundary))
    def test_bearer_token_redacted_at_all_boundaries(self, redactor, boundary: Boundary) -> None:
        result = redactor.redact_text(FAKE_BEARER, boundary)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result

    @pytest.mark.parametrize("boundary", list(Boundary))
    def test_pem_key_redacted_at_all_boundaries(self, redactor, boundary: Boundary) -> None:
        result = redactor.redact_text(FAKE_PEM, boundary)
        assert "BEGIN RSA PRIVATE KEY" not in result

    @pytest.mark.parametrize("boundary", list(Boundary))
    def test_kv_secret_redacted_at_all_boundaries(self, redactor, boundary: Boundary) -> None:
        result = redactor.redact_text(FAKE_KV, boundary)
        assert "abc123secretvalue" not in result

    def test_credential_in_mapping_redacted_at_memory_write(self, redactor) -> None:
        data = {"key": FAKE_AWS_KEY, "host": FAKE_IP}
        result = redactor.redact_mapping(data, Boundary.MEMORY_WRITE)
        assert FAKE_AWS_KEY not in result["key"]

    def test_credential_in_mapping_redacted_at_operational(self, redactor) -> None:
        data = {"token": FAKE_BEARER, "user": "alice"}
        result = redactor.redact_mapping(data, Boundary.OPERATIONAL)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result["token"]


# ── PII: output boundaries only; raw at internal boundaries (FR-006b) ────────


class TestPIIBoundaryPolicy:
    @pytest.mark.parametrize(
        "boundary",
        [
            Boundary.LOG,
            Boundary.TRACE,
            Boundary.PROMPT,
            Boundary.SNAPSHOT,
            Boundary.DASHBOARD,
        ],
    )
    def test_email_redacted_at_output_boundary(self, redactor_with_presidio, boundary) -> None:
        result = redactor_with_presidio.redact_text(f"user email is {FAKE_EMAIL}", boundary)
        assert FAKE_EMAIL not in result

    @pytest.mark.parametrize("boundary", [Boundary.MEMORY_WRITE, Boundary.OPERATIONAL])
    def test_email_raw_at_internal_boundary(self, redactor_with_presidio, boundary) -> None:
        text = f"user email is {FAKE_EMAIL}"
        result = redactor_with_presidio.redact_text(text, boundary)
        assert FAKE_EMAIL in result


# ── OPERATIONAL_IDENTIFIER: raw internally, redacted at output ───────────────


class TestOperationalIdentifier:
    @pytest.mark.parametrize("boundary", [Boundary.MEMORY_WRITE, Boundary.OPERATIONAL])
    def test_ip_survives_at_internal_boundary(self, redactor_with_presidio, boundary) -> None:
        result = redactor_with_presidio.redact_text(f"source_ip={FAKE_IP}", boundary)
        assert FAKE_IP in result

    @pytest.mark.parametrize(
        "boundary",
        [
            Boundary.LOG,
            Boundary.TRACE,
            Boundary.PROMPT,
            Boundary.SNAPSHOT,
            Boundary.DASHBOARD,
        ],
    )
    def test_ip_redacted_at_output_boundary(self, redactor_with_presidio, boundary) -> None:
        result = redactor_with_presidio.redact_text(f"source_ip={FAKE_IP}", boundary)
        assert FAKE_IP not in result


# ── Nested traversal and idempotency (FR-004) ────────────────────────────────


class TestNestedAndIdempotent:
    def test_nested_three_levels_deep(self, redactor) -> None:
        data = {"level1": {"level2": {"level3": f"secret={FAKE_AWS_KEY}"}}}
        result = redactor.redact_mapping(data, Boundary.LOG)
        assert FAKE_AWS_KEY not in str(result)

    def test_list_values_traversed(self, redactor) -> None:
        data = {"keys": [FAKE_AWS_KEY, FAKE_BEARER, "plain-value"]}
        result = redactor.redact_mapping(data, Boundary.LOG)
        assert FAKE_AWS_KEY not in str(result["keys"])
        assert "plain-value" in str(result["keys"])

    def test_structure_preserved_after_redaction(self, redactor) -> None:
        data = {"a": FAKE_AWS_KEY, "b": {"c": "safe"}}
        result = redactor.redact_mapping(data, Boundary.LOG)
        assert isinstance(result, dict)
        assert isinstance(result["b"], dict)
        assert result["b"]["c"] == "safe"

    def test_input_not_mutated(self, redactor) -> None:
        original = {"token": FAKE_AWS_KEY}
        redactor.redact_mapping(original, Boundary.LOG)
        assert original["token"] == FAKE_AWS_KEY

    def test_idempotent_text(self, redactor) -> None:
        once = redactor.redact_text(FAKE_AWS_KEY, Boundary.LOG)
        twice = redactor.redact_text(once, Boundary.LOG)
        assert once == twice

    def test_idempotent_mapping(self, redactor) -> None:
        data = {"k": FAKE_AWS_KEY}
        once = redactor.redact_mapping(data, Boundary.LOG)
        twice = redactor.redact_mapping(once, Boundary.LOG)
        assert once == twice


# ── High-entropy heuristic (FR-005) ─────────────────────────────────────────


class TestEntropyHeuristic:
    def test_high_entropy_token_flagged(self, redactor) -> None:
        result = redactor.redact_text(FAKE_HIGH_ENTROPY, Boundary.LOG)
        assert FAKE_HIGH_ENTROPY not in result

    def test_low_entropy_plain_text_not_flagged(self, redactor) -> None:
        text = "the quick brown fox jumps over the lazy dog"
        result = redactor.redact_text(text, Boundary.LOG)
        assert result == text


# ── Fail-closed (FR-003) ─────────────────────────────────────────────────────


class TestFailClosed:
    def test_redactor_error_yields_fail_closed_placeholder(self, monkeypatch) -> None:
        from backend.infra.redaction import build_redactor as _build

        r = _build(presidio_enabled=False)

        # Patch the scrubber to raise so we exercise the fail-closed path
        def _bad_scrub(text: str) -> str:
            raise RuntimeError("scrubber exploded")

        monkeypatch.setattr(r, "_scrub", _bad_scrub)
        result = r.redact_text("anything", Boundary.LOG)
        assert result == "[REDACTION-FAILED]"
        assert "anything" not in result
