"""JevClient — optional, config-gated typed-decision client for TypeSafe Jev (JD1/JD6).

httpx is the only transport; no vendor SDK. One pooled AsyncClient per lifespan,
short timeout, no retries — callers fall back to the LLM on any DecisionError (JD3).
Jev is off by default; a missing API key disables it rather than failing boot (JD5).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from typing import Any

import httpx
from pydantic import ValidationError

from backend.domain.decision import (
    ChoiceAnswer,
    DecisionError,
    DecisionErrorKind,
    DecisionRequest,
    DecisionResponse,
)
from backend.domain.redaction import Boundary
from backend.domain.telemetry import SpanKind
from backend.infra.logging import get_logger

logger = get_logger(__name__)


def _error_kind_for_status(status: int) -> DecisionErrorKind:
    if status in (401, 403):
        return DecisionErrorKind.AUTH
    if status in (400, 404, 422):
        return DecisionErrorKind.INVALID_REQUEST
    if status == 429:
        return DecisionErrorKind.RATE_LIMITED
    return DecisionErrorKind.UNAVAILABLE


class JevClient:
    """DecisionClient over POST /v1/systemone (choice questions only)."""

    def __init__(
        self,
        *,
        cfg: Any,
        api_key: str,
        redactor: Any,
        tracer: Any,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._cfg = cfg
        self._redactor = redactor
        self._tracer = tracer
        self._http = http_client or httpx.AsyncClient(timeout=cfg.timeout_s)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def aclose(self) -> None:
        await self._http.aclose()

    async def decide(self, request: DecisionRequest, *, correlation_id: str) -> DecisionResponse:
        from backend.infra.tracing import span

        body = self._build_body(request)
        with span(
            self._tracer,
            "jev_decide",
            SpanKind.LLM_CALL,
            correlation_id,
            attrs={"jev.questions": ",".join(request.questions)},
        ) as s:
            response = await self._post(body)
            parsed = self._parse(response, request)
            s.llm_model = parsed.model
            s.tokens_in = parsed.input_tokens
            s.tokens_out = parsed.output_tokens
        return parsed

    def _build_body(self, request: DecisionRequest) -> dict[str, Any]:
        state = request.state
        if isinstance(state, str):
            # Same credential scrub as the LLM seam (JD5).
            state = self._redactor.redact_text(state, Boundary.OPERATIONAL)
        return {
            "model": self._cfg.model,
            "state": state,
            "questions": {
                qid: {"type": "choice", "instructions": q.instructions, "criteria": q.criteria}
                for qid, q in request.questions.items()
            },
        }

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._http.post(
                self._cfg.base_url, json=body, headers=self._headers, timeout=self._cfg.timeout_s
            )
        except httpx.TimeoutException as exc:
            raise DecisionError(DecisionErrorKind.TIMEOUT) from exc
        except httpx.HTTPError as exc:
            raise DecisionError(DecisionErrorKind.UNAVAILABLE, type(exc).__name__) from exc

        if resp.status_code != 200:
            raise DecisionError(
                _error_kind_for_status(resp.status_code), f"http {resp.status_code}"
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise DecisionError(DecisionErrorKind.MALFORMED, "non-json body") from exc
        if not isinstance(data, dict):
            raise DecisionError(DecisionErrorKind.MALFORMED, "body is not an object")
        return data

    @staticmethod
    def _parse(data: dict[str, Any], request: DecisionRequest) -> DecisionResponse:
        raw_answers = data.get("answers")
        if not isinstance(raw_answers, dict):
            raise DecisionError(DecisionErrorKind.MALFORMED, "missing answers")

        answers: dict[str, ChoiceAnswer] = {}
        for qid, question in request.questions.items():
            raw = raw_answers.get(qid)
            if not isinstance(raw, dict) or raw.get("type") != "choice":
                raise DecisionError(DecisionErrorKind.MALFORMED, f"no choice answer for {qid}")
            try:
                answer = ChoiceAnswer(
                    choice=raw.get("choice"),
                    confidence=raw.get("confidence"),
                    probabilities=raw.get("probabilities") or {},
                )
            except ValidationError as exc:
                raise DecisionError(
                    DecisionErrorKind.MALFORMED, f"invalid answer for {qid}"
                ) from exc
            if answer.choice not in question.criteria:
                raise DecisionError(DecisionErrorKind.MALFORMED, f"unknown option for {qid}")
            answers[qid] = answer

        usage = data.get("usage") or {}
        return DecisionResponse(
            model=str(data.get("model") or "unknown"),
            answers=answers,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )


# ── Provider ─────────────────────────────────────────────────────────────────


class DecisionProvider:
    """Lifespan singleton — yields a JevClient, or None when disabled/unconfigured."""

    name = "decision"

    @contextlib.asynccontextmanager
    async def build(self, settings: Any) -> AsyncGenerator[JevClient | None, None]:
        cfg = getattr(settings, "jev", None)
        if cfg is None or not cfg.enabled:
            logger.info("jev_disabled")
            yield None
            return

        container = getattr(settings, "_container", None)
        api_key: str | None = None
        try:
            secret = await container.vault_client.fetch_secret(cfg.api_key_vault_path)
            api_key = secret.get("api_key") or None
        except Exception as exc:
            logger.warning("jev_api_key_unavailable", error=str(exc))

        obs = getattr(container, "observability", None) if container else None
        if not api_key or obs is None:
            logger.info("jev_unconfigured_disabled", has_key=bool(api_key))
            yield None
            return

        client = JevClient(cfg=cfg, api_key=api_key, redactor=obs.redactor, tracer=obs.tracer)
        logger.info("jev_provider_ready", model=cfg.model)
        try:
            yield client
        finally:
            await client.aclose()
            logger.info("jev_provider_disposed")
