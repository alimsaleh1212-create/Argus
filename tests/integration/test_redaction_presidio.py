"""Integration tests — T007: real in-process Presidio PII detection.

No testcontainer needed — Presidio runs in-process. These are marked
`integration` because they load the en_core_web_sm spaCy model (~12 MB),
which is too slow for the unit tier.
"""

from __future__ import annotations

import pytest

from backend.domain.redaction import Boundary
from backend.infra.redaction import build_redactor

pytestmark = pytest.mark.integration

FAKE_EMAIL = "bob.smith@acme.org"
FAKE_PHONE = "555-867-5309"
FAKE_CC = "4111111111111111"


@pytest.fixture(scope="module")
def presidio_redactor():
    return build_redactor(presidio_enabled=True)


class TestPresidioDetection:
    def test_email_detected(self, presidio_redactor) -> None:
        result = presidio_redactor.redact_text(
            f"contact us at {FAKE_EMAIL}", Boundary.LOG
        )
        assert FAKE_EMAIL not in result

    def test_phone_detected(self, presidio_redactor) -> None:
        result = presidio_redactor.redact_text(
            f"call {FAKE_PHONE} for support", Boundary.LOG
        )
        assert FAKE_PHONE not in result

    def test_credit_card_detected(self, presidio_redactor) -> None:
        result = presidio_redactor.redact_text(
            f"card number {FAKE_CC}", Boundary.LOG
        )
        assert FAKE_CC not in result

    def test_deterministic_toggle_leaves_pattern_only(self) -> None:
        """With presidio_enabled=False, pattern scrubber still catches credentials."""
        from backend.infra.redaction import build_redactor

        r = build_redactor(presidio_enabled=False)
        # AWS key — caught by explicit pattern
        result = r.redact_text("AKIAIOSFODNN7EXAMPLE", Boundary.LOG)
        assert "AKIAIOSFODNN7EXAMPLE" not in result

    def test_presidio_off_leaves_email_raw(self) -> None:
        """With presidio_enabled=False, email (PII, NER) is NOT redacted."""
        from backend.infra.redaction import build_redactor

        r = build_redactor(presidio_enabled=False)
        result = r.redact_text(f"email={FAKE_EMAIL}", Boundary.LOG)
        assert FAKE_EMAIL in result
