"""T040 — Rationale gate redaction guard.

Asserts that when a rationale or incident_context contains a planted secret,
the judge function receives the REDACTED version — the unredacted secret MUST
NOT appear in what the judge sees (FR-014, RD8).

This is a unit-tier test: we intercept the judge call and inspect its inputs
rather than calling a real LLM. The redactor under test is the
build_redactor(presidio_enabled=False) variant — avoids needing the full
Presidio model at test time (pattern consistent with SPEC-observability #2 unit tests).
"""

from __future__ import annotations

import pytest

PLANTED_AWS_KEY = "AKIAIOSFODNN7EXAMPLE"
PLANTED_BEARER = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test.sig"
PLANTED_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"


@pytest.mark.asyncio
async def test_judge_prompt_has_no_planted_aws_key():
    """incident_context with planted AWS key → judge receives redacted context."""
    from backend.eval.judge import judge_rationale
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    captured: list[str] = []

    class _FakeLlm:
        class _FakeResponse:
            content = "grounded"

        async def generate(self, *, system: str, user: str, max_tokens: int = 16):
            captured.append(user)
            return self._FakeResponse()

    await judge_rationale(
        incident_context=f"Alert from AKID={PLANTED_AWS_KEY} on host prod-01",
        rationale_text="The alert originated from an AWS role and appears legitimate.",
        llm_client=_FakeLlm(),
        redactor=redactor,
    )

    assert captured, "judge_rationale did not call llm_client.generate"
    judge_input = captured[0]
    assert PLANTED_AWS_KEY not in judge_input, (
        f"Planted AWS key found unredacted in judge prompt:\n{judge_input}"
    )


@pytest.mark.asyncio
async def test_judge_prompt_has_no_planted_bearer_token():
    """rationale_text with planted bearer token → judge receives redacted text."""
    from backend.eval.judge import judge_rationale
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    captured: list[str] = []

    class _FakeLlm:
        class _FakeResponse:
            content = "partially_grounded"

        async def generate(self, *, system: str, user: str, max_tokens: int = 16):
            captured.append(user)
            return self._FakeResponse()

    await judge_rationale(
        incident_context="Normal incident context without secrets.",
        rationale_text=(
            f"Auth header used was {PLANTED_BEARER}. "
            "This indicates token exfiltration."
        ),
        llm_client=_FakeLlm(),
        redactor=redactor,
    )

    judge_input = captured[0]
    assert PLANTED_BEARER not in judge_input, (
        f"Planted bearer token found unredacted in judge prompt:\n{judge_input}"
    )


@pytest.mark.asyncio
async def test_judge_prompt_has_no_planted_pem_block():
    """PEM block in context → judge receives redacted version."""
    from backend.eval.judge import judge_rationale
    from backend.infra.redaction import build_redactor

    redactor = build_redactor(presidio_enabled=False)
    captured: list[str] = []

    class _FakeLlm:
        class _FakeResponse:
            content = "ungrounded"

        async def generate(self, *, system: str, user: str, max_tokens: int = 16):
            captured.append(user)
            return self._FakeResponse()

    await judge_rationale(
        incident_context=(
            f"SSH key exfiltration detected. Key contents:\n{PLANTED_PEM}"
        ),
        rationale_text="The private key was accessed without authorization.",
        llm_client=_FakeLlm(),
        redactor=redactor,
    )

    judge_input = captured[0]
    assert "BEGIN RSA PRIVATE KEY" not in judge_input, (
        f"PEM block found unredacted in judge prompt:\n{judge_input}"
    )
