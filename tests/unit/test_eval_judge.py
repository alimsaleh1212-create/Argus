"""Coverage for backend/eval/judge.py: fallback label and judge_cites_evidence."""

from __future__ import annotations

import pytest


class _FakeRedactor:
    def redact_text(self, text: str, boundary) -> str:
        return text  # passthrough in tests


class _FakeLlm:
    def __init__(self, response_content: str):
        self._content = response_content

    class _Resp:
        def __init__(self, content: str):
            self.content = content

    async def generate(self, *, system: str, user: str, max_tokens: int = 16):
        return self._Resp(self._content)


@pytest.mark.asyncio
async def test_judge_rationale_valid_label_returned():
    from backend.eval.judge import judge_rationale

    result = await judge_rationale(
        incident_context="ctx",
        rationale_text="rat",
        llm_client=_FakeLlm("grounded"),
        redactor=_FakeRedactor(),
    )
    assert result == "grounded"


@pytest.mark.asyncio
async def test_judge_rationale_unknown_label_falls_back():
    """Model returns unexpected label → fallback to partially_grounded (conservative)."""
    from backend.eval.judge import judge_rationale

    result = await judge_rationale(
        incident_context="ctx",
        rationale_text="rat",
        llm_client=_FakeLlm("IRRELEVANT_LABEL"),
        redactor=_FakeRedactor(),
    )
    assert result == "partially_grounded"


@pytest.mark.asyncio
async def test_judge_cites_evidence_yes():
    from backend.eval.judge import judge_cites_evidence

    result = await judge_cites_evidence(
        incident_context="ctx",
        rationale_text="rat",
        llm_client=_FakeLlm("YES"),
        redactor=_FakeRedactor(),
    )
    assert result is True


@pytest.mark.asyncio
async def test_judge_cites_evidence_no():
    from backend.eval.judge import judge_cites_evidence

    result = await judge_cites_evidence(
        incident_context="ctx",
        rationale_text="rat",
        llm_client=_FakeLlm("NO"),
        redactor=_FakeRedactor(),
    )
    assert result is False


@pytest.mark.asyncio
async def test_judge_cites_evidence_partial_yes():
    """Any response starting with Y (case-insensitive) is treated as YES."""
    from backend.eval.judge import judge_cites_evidence

    result = await judge_cites_evidence(
        incident_context="ctx",
        rationale_text="rat",
        llm_client=_FakeLlm("Yes, it does."),
        redactor=_FakeRedactor(),
    )
    assert result is True
