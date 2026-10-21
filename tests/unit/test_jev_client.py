"""JevClient — HTTP mapping, answer validation, error kinds, redaction (JD1/JD5/JD6)."""

from __future__ import annotations

import json

import httpx
import pytest

from backend.domain.decision import (
    ChoiceQuestion,
    DecisionError,
    DecisionErrorKind,
    DecisionRequest,
)
from backend.infra.config import JevSettings
from backend.infra.decision import JevClient
from backend.infra.redaction import build_redactor
from backend.infra.tracing import build_tracer

_QUESTION = ChoiceQuestion(
    instructions="Classify.",
    criteria={"real": "a threat", "noise": "benign", "uncertain": "unclear"},
)


def _request(state: str = "alert evidence") -> DecisionRequest:
    return DecisionRequest(state=state, questions={"triage_verdict": _QUESTION})


def _ok_body(choice: str = "real", confidence: float = 0.91) -> dict:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "triage_verdict": {
                "type": "choice",
                "choice": choice,
                "confidence": confidence,
                "probabilities": {"real": 0.91, "noise": 0.06, "uncertain": 0.03},
            }
        },
        "usage": {"input_tokens": 120, "output_tokens": 4},
    }


def _client(handler) -> tuple[JevClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(_record))
    client = JevClient(
        cfg=JevSettings(enabled=True),
        api_key="test-key",
        redactor=build_redactor(presidio_enabled=False),
        tracer=build_tracer(exporter=None),
        http_client=http,
    )
    return client, seen


async def test_decide_parses_choice_answer_and_usage():
    client, seen = _client(lambda r: httpx.Response(200, json=_ok_body()))

    resp = await client.decide(_request(), correlation_id="corr-1")

    answer = resp.answers["triage_verdict"]
    assert answer.choice == "real"
    assert answer.confidence == pytest.approx(0.91)
    assert answer.probabilities["noise"] == pytest.approx(0.06)
    assert resp.model == "jev-1.13.0"
    assert resp.total_tokens == 124

    body = json.loads(seen[0].content)
    assert body["model"] == "jev-1.13.0"  # pinned version, not jev-latest (JD6)
    assert body["questions"]["triage_verdict"]["type"] == "choice"
    assert body["questions"]["triage_verdict"]["criteria"] == _QUESTION.criteria
    assert seen[0].headers["Authorization"] == "Bearer test-key"


async def test_state_is_credential_scrubbed_before_sending():
    client, seen = _client(lambda r: httpx.Response(200, json=_ok_body()))
    secret = "AKIAIOSFODNN7EXAMPLE"

    await client.decide(_request(f"aws key {secret} seen in log"), correlation_id="c")

    assert secret not in json.loads(seen[0].content)["state"]


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, DecisionErrorKind.AUTH),
        (422, DecisionErrorKind.INVALID_REQUEST),
        (429, DecisionErrorKind.RATE_LIMITED),
        (529, DecisionErrorKind.UNAVAILABLE),
        (500, DecisionErrorKind.UNAVAILABLE),
    ],
)
async def test_http_errors_map_to_kinds(status: int, kind: DecisionErrorKind):
    client, _ = _client(lambda r: httpx.Response(status, json={"detail": "x"}))

    with pytest.raises(DecisionError) as exc_info:
        await client.decide(_request(), correlation_id="c")
    assert exc_info.value.kind == kind


async def test_timeout_maps_to_timeout():
    def _raise(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client, _ = _client(_raise)

    with pytest.raises(DecisionError) as exc_info:
        await client.decide(_request(), correlation_id="c")
    assert exc_info.value.kind == DecisionErrorKind.TIMEOUT


@pytest.mark.parametrize(
    "body",
    [
        {"model": "jev", "answers": {}},  # missing question
        _ok_body(choice="maybe"),  # option not in criteria
        _ok_body(confidence=1.7),  # confidence out of range
        {"model": "jev"},  # no answers at all
    ],
)
async def test_malformed_answers_are_rejected(body: dict):
    client, _ = _client(lambda r: httpx.Response(200, json=body))

    with pytest.raises(DecisionError) as exc_info:
        await client.decide(_request(), correlation_id="c")
    assert exc_info.value.kind == DecisionErrorKind.MALFORMED


async def test_non_json_body_is_malformed():
    client, _ = _client(lambda r: httpx.Response(200, content=b"<html>"))

    with pytest.raises(DecisionError) as exc_info:
        await client.decide(_request(), correlation_id="c")
    assert exc_info.value.kind == DecisionErrorKind.MALFORMED


def test_jev_disabled_by_default():
    cfg = JevSettings()
    assert cfg.enabled is False
    assert cfg.model != "jev-latest"
