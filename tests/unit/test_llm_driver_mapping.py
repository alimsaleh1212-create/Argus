"""Unit tests for llm_drivers.py pure helpers and driver methods.

Exercises error classifiers, stop-reason mappers, message builders, response
parsers, and driver exception paths via mocked SDKs (zero real network calls).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.domain.llm import (
    LlmMessage,
    LlmRequest,
    ProviderId,
    StopReason,
    ToolSpec,
)

# ---------------------------------------------------------------------------
# Error classifiers
# ---------------------------------------------------------------------------


class TestClassifyGeminiError:
    def test_auth_by_status_code(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        exc = Exception("401 Unauthorized")
        assert _classify_gemini_error(exc) == LlmErrorKind.AUTH

    def test_auth_by_api_key_message(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        exc = Exception("invalid api_key provided")
        assert _classify_gemini_error(exc) == LlmErrorKind.AUTH

    def test_auth_by_exception_type(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        class PermissionDeniedError(Exception):
            pass

        assert _classify_gemini_error(PermissionDeniedError("denied")) == LlmErrorKind.AUTH

    def test_invalid_request_by_400(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        exc = Exception("400 bad request: context window exceeded")
        assert _classify_gemini_error(exc) == LlmErrorKind.INVALID_REQUEST

    def test_invalid_request_by_type(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        class InvalidArgumentError(Exception):
            pass

        assert _classify_gemini_error(InvalidArgumentError("bad")) == LlmErrorKind.INVALID_REQUEST

    def test_content_refusal(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        exc = Exception("safety filter triggered — harm detected")
        assert _classify_gemini_error(exc) == LlmErrorKind.CONTENT_REFUSAL

    def test_transient_by_429(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        exc = Exception("429 rate limit exceeded")
        assert _classify_gemini_error(exc) == LlmErrorKind.TRANSIENT

    def test_transient_default_unknown(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_gemini_error

        exc = Exception("some unknown weirdness")
        assert _classify_gemini_error(exc) == LlmErrorKind.TRANSIENT


class TestClassifyOllamaError:
    def test_connection_refused_is_transient(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_ollama_error

        exc = Exception("connection refused")
        assert _classify_ollama_error(exc) == LlmErrorKind.TRANSIENT

    def test_connect_error_type_is_transient(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_ollama_error

        class ConnectError(Exception):
            pass

        assert _classify_ollama_error(ConnectError("no route")) == LlmErrorKind.TRANSIENT

    def test_model_not_found_is_invalid_request(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_ollama_error

        exc = Exception("model 'qwen2:xyz' not found")
        assert _classify_ollama_error(exc) == LlmErrorKind.INVALID_REQUEST

    def test_unknown_is_transient(self):
        from backend.infra.llm_drivers import LlmErrorKind, _classify_ollama_error

        exc = Exception("some random error")
        assert _classify_ollama_error(exc) == LlmErrorKind.TRANSIENT


class TestSafeMessage:
    def test_truncates_long_message(self):
        from backend.infra.llm_drivers import _safe_message

        exc = ValueError("x" * 300)
        msg = _safe_message(exc)
        assert "ValueError" in msg
        assert len(msg) < 250

    def test_includes_type_and_message(self):
        from backend.infra.llm_drivers import _safe_message

        exc = RuntimeError("something went wrong")
        msg = _safe_message(exc)
        assert "RuntimeError" in msg
        assert "something went wrong" in msg


# ---------------------------------------------------------------------------
# Stop-reason mappers
# ---------------------------------------------------------------------------


class TestStopReasonMappers:
    def test_gemini_stop(self):
        from backend.infra.llm_drivers import _gemini_finish_reason_to_stop

        assert _gemini_finish_reason_to_stop("STOP") == StopReason.END_TURN

    def test_gemini_max_tokens(self):
        from backend.infra.llm_drivers import _gemini_finish_reason_to_stop

        assert _gemini_finish_reason_to_stop("MAX_TOKENS") == StopReason.MAX_TOKENS

    def test_gemini_safety(self):
        from backend.infra.llm_drivers import _gemini_finish_reason_to_stop

        assert _gemini_finish_reason_to_stop("SAFETY") == StopReason.CONTENT_FILTER

    def test_gemini_unknown_reason(self):
        from backend.infra.llm_drivers import _gemini_finish_reason_to_stop

        assert _gemini_finish_reason_to_stop("WHATEVER") == StopReason.UNKNOWN

    def test_ollama_stop(self):
        from backend.infra.llm_drivers import _ollama_done_reason_to_stop

        assert _ollama_done_reason_to_stop("stop") == StopReason.END_TURN

    def test_ollama_length(self):
        from backend.infra.llm_drivers import _ollama_done_reason_to_stop

        assert _ollama_done_reason_to_stop("length") == StopReason.MAX_TOKENS

    def test_ollama_tool_calls(self):
        from backend.infra.llm_drivers import _ollama_done_reason_to_stop

        assert _ollama_done_reason_to_stop("tool_calls") == StopReason.TOOL_USE

    def test_ollama_none_reason(self):
        from backend.infra.llm_drivers import _ollama_done_reason_to_stop

        assert _ollama_done_reason_to_stop(None) == StopReason.UNKNOWN


# ---------------------------------------------------------------------------
# Message / config builders
# ---------------------------------------------------------------------------


class TestBuildGeminiContents:
    def test_user_message(self):
        from backend.infra.llm_drivers import _build_gemini_contents

        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        contents, system = _build_gemini_contents(req)
        assert system is None
        assert len(contents) == 1
        assert contents[0].role == "user"

    def test_assistant_message(self):
        from backend.infra.llm_drivers import _build_gemini_contents

        req = LlmRequest(messages=[LlmMessage(role="assistant", content="hi")])
        contents, _ = _build_gemini_contents(req)
        assert contents[0].role == "model"

    def test_system_message_extracted(self):
        from backend.infra.llm_drivers import _build_gemini_contents

        req = LlmRequest(
            messages=[
                LlmMessage(role="system", content="You are a helpful assistant."),
                LlmMessage(role="user", content="hello"),
            ]
        )
        contents, system = _build_gemini_contents(req)
        assert "helpful" in system
        assert len(contents) == 1  # system is extracted, not a content item

    def test_tool_message_becomes_function_response(self):
        from backend.infra.llm_drivers import _build_gemini_contents

        req = LlmRequest(
            messages=[
                LlmMessage(role="tool", content='{"result": "ok"}', name="search"),
            ]
        )
        contents, _ = _build_gemini_contents(req)
        assert len(contents) == 1
        part = contents[0].parts[0]
        assert part.function_response is not None
        assert part.function_response.name == "search"


_DUMMY_MSG = [LlmMessage(role="user", content="x")]


class TestBuildGeminiConfig:
    def test_temperature_passed(self):
        from backend.infra.llm_drivers import _build_gemini_config

        req = LlmRequest(messages=_DUMMY_MSG, temperature=0.5)
        cfg = _build_gemini_config(req, None)
        assert cfg.temperature == 0.5

    def test_max_tokens_passed(self):
        from backend.infra.llm_drivers import _build_gemini_config

        req = LlmRequest(messages=_DUMMY_MSG, max_tokens=100)
        cfg = _build_gemini_config(req, None)
        assert cfg.max_output_tokens == 100

    def test_top_p_passed(self):
        from backend.infra.llm_drivers import _build_gemini_config

        req = LlmRequest(messages=_DUMMY_MSG, top_p=0.9)
        cfg = _build_gemini_config(req, None)
        assert cfg.top_p == 0.9

    def test_system_instruction_set(self):
        from backend.infra.llm_drivers import _build_gemini_config

        req = LlmRequest(messages=_DUMMY_MSG)
        cfg = _build_gemini_config(req, "Be concise.")
        assert cfg.system_instruction == "Be concise."

    def test_tools_added(self):
        from backend.infra.llm_drivers import _build_gemini_config

        tool = ToolSpec(name="search", description="Search the web", parameters={"type": "object"})
        req = LlmRequest(messages=_DUMMY_MSG, tools=[tool])
        cfg = _build_gemini_config(req, None)
        assert cfg.tools is not None
        assert len(cfg.tools) == 1

    def test_structured_output_schema(self):
        from backend.infra.llm_drivers import _build_gemini_config

        schema = {"type": "object", "required": ["verdict"]}
        req = LlmRequest(messages=_DUMMY_MSG, response_schema=schema)
        cfg = _build_gemini_config(req, None)
        assert cfg.response_mime_type == "application/json"
        assert cfg.response_schema == schema

    def test_require_tool_any_mode(self):
        from backend.infra.llm_drivers import _build_gemini_config

        tool = ToolSpec(name="alert", description="...", parameters={})
        req = LlmRequest(messages=_DUMMY_MSG, tools=[tool], require_tool="alert")
        cfg = _build_gemini_config(req, None)
        assert cfg.tool_config is not None


# ---------------------------------------------------------------------------
# Response parsers
# ---------------------------------------------------------------------------


class TestParseGeminiResponse:
    def _raw(
        self, text="ok", function_calls=None, prompt_tokens=10, completion_tokens=5, reason="STOP"
    ):
        r = MagicMock()
        r.text = text
        r.function_calls = function_calls or []
        r.usage_metadata = MagicMock()
        r.usage_metadata.prompt_token_count = prompt_tokens
        r.usage_metadata.candidates_token_count = completion_tokens
        r.candidates = [MagicMock()]
        r.candidates[0].finish_reason = MagicMock()
        r.candidates[0].finish_reason.name = reason
        return r

    def test_content_and_usage(self):
        from backend.infra.llm_drivers import _parse_gemini_response

        r = _parse_gemini_response(self._raw(text="hello"), "gemini-1.5-flash")
        assert r.content == "hello"
        assert r.usage.prompt_tokens == 10
        assert r.usage.completion_tokens == 5

    def test_stop_reason_stop(self):
        from backend.infra.llm_drivers import _parse_gemini_response

        r = _parse_gemini_response(self._raw(reason="STOP"), "gemini-1.5-flash")
        assert r.stop_reason == StopReason.END_TURN

    def test_tool_calls_parsed(self):
        from backend.infra.llm_drivers import _parse_gemini_response

        fc = MagicMock()
        fc.name = "search"
        fc.args = {"q": "test"}
        r = _parse_gemini_response(self._raw(function_calls=[fc]), "gemini-1.5-flash")
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].name == "search"
        assert r.stop_reason == StopReason.TOOL_USE

    def test_missing_candidates_defaults_to_unknown(self):
        from backend.infra.llm_drivers import _parse_gemini_response

        raw = MagicMock()
        raw.text = "hi"
        raw.function_calls = []
        raw.usage_metadata = MagicMock()
        raw.usage_metadata.prompt_token_count = None
        raw.usage_metadata.candidates_token_count = None
        raw.candidates = []  # no candidates
        r = _parse_gemini_response(raw, "gemini-1.5-flash")
        assert r.stop_reason == StopReason.UNKNOWN


class TestParseOllamaResponse:
    def _raw(
        self, content="ok", tool_calls=None, prompt_eval_count=4, eval_count=2, done_reason="stop"
    ):
        r = SimpleNamespace(
            message=SimpleNamespace(content=content, tool_calls=tool_calls or []),
            prompt_eval_count=prompt_eval_count,
            eval_count=eval_count,
            done_reason=done_reason,
        )
        return r

    def test_content_and_usage(self):
        from backend.infra.llm_drivers import _parse_ollama_response

        r = _parse_ollama_response(self._raw(content="hello"), "qwen2:0.5b")
        assert r.content == "hello"
        assert r.usage.prompt_tokens == 4
        assert r.usage.completion_tokens == 2

    def test_stop_reason(self):
        from backend.infra.llm_drivers import _parse_ollama_response

        r = _parse_ollama_response(self._raw(done_reason="stop"), "qwen2:0.5b")
        assert r.stop_reason == StopReason.END_TURN

    def test_length_reason(self):
        from backend.infra.llm_drivers import _parse_ollama_response

        r = _parse_ollama_response(self._raw(done_reason="length"), "qwen2:0.5b")
        assert r.stop_reason == StopReason.MAX_TOKENS

    def test_tool_calls_parsed(self):
        from backend.infra.llm_drivers import _parse_ollama_response

        tc = SimpleNamespace(function=SimpleNamespace(name="search", arguments={"q": "test"}))
        r = _parse_ollama_response(self._raw(tool_calls=[tc]), "qwen2:0.5b")
        assert len(r.tool_calls) == 1
        assert r.tool_calls[0].name == "search"
        assert r.stop_reason == StopReason.TOOL_USE

    def test_tool_call_string_args_parsed(self):
        from backend.infra.llm_drivers import _parse_ollama_response

        tc = SimpleNamespace(function=SimpleNamespace(name="search", arguments='{"q": "test"}'))
        r = _parse_ollama_response(self._raw(tool_calls=[tc]), "qwen2:0.5b")
        assert r.tool_calls[0].arguments == {"q": "test"}


class TestBuildOllamaMessages:
    def test_user_message(self):
        from backend.infra.llm_drivers import _build_ollama_messages

        req = LlmRequest(messages=[LlmMessage(role="user", content="hi")])
        msgs = _build_ollama_messages(req)
        assert msgs == [{"role": "user", "content": "hi"}]

    def test_system_in_messages(self):
        from backend.infra.llm_drivers import _build_ollama_messages

        req = LlmRequest(messages=[LlmMessage(role="system", content="You are helpful.")])
        msgs = _build_ollama_messages(req)
        assert msgs[0]["role"] == "system"

    def test_top_level_system_prepended(self):
        from backend.infra.llm_drivers import _build_ollama_messages

        req = LlmRequest(
            messages=[LlmMessage(role="user", content="hi")],
            system="Be concise.",
        )
        msgs = _build_ollama_messages(req)
        assert msgs[0] == {"role": "system", "content": "Be concise."}
        assert msgs[1]["role"] == "user"

    def test_tool_message(self):
        from backend.infra.llm_drivers import _build_ollama_messages

        req = LlmRequest(messages=[LlmMessage(role="tool", content='{"r":1}', name="search")])
        msgs = _build_ollama_messages(req)
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["name"] == "search"


# ---------------------------------------------------------------------------
# Driver exception paths (SDK mocked)
# ---------------------------------------------------------------------------


class TestGeminiDriverExceptionPaths:
    async def test_exception_from_generate_content_raises_llm_error(self):
        from backend.domain.llm import LlmError
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import GeminiDriver

        settings = LlmSettings()

        with patch("backend.infra.llm_drivers.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.generate_content = AsyncMock(
                side_effect=RuntimeError("503 service unavailable")
            )

            driver = GeminiDriver(settings, api_key="fake")
            req = LlmRequest(messages=[LlmMessage(role="user", content="hi")])
            with pytest.raises(LlmError):
                await driver.generate(req)

    async def test_ping_returns_true_when_list_succeeds(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import GeminiDriver

        settings = LlmSettings()

        with patch("backend.infra.llm_drivers.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.list = AsyncMock(return_value=[])

            driver = GeminiDriver(settings, api_key="fake")
            result = await driver.ping()

        assert result is True

    async def test_ping_returns_false_when_list_raises(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import GeminiDriver

        settings = LlmSettings()

        with patch("backend.infra.llm_drivers.genai") as mock_genai:
            mock_client = MagicMock()
            mock_genai.Client.return_value = mock_client
            mock_client.aio = MagicMock()
            mock_client.aio.models = MagicMock()
            mock_client.aio.models.list = AsyncMock(side_effect=Exception("unreachable"))

            driver = GeminiDriver(settings, api_key="fake")
            result = await driver.ping()

        assert result is False


class TestOllamaDriverMocked:
    async def test_generate_calls_client_chat(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_model="qwen2:0.5b")

        raw = SimpleNamespace(
            message=SimpleNamespace(content="hi there", tool_calls=[]),
            prompt_eval_count=3,
            eval_count=2,
            done_reason="stop",
        )

        with patch("backend.infra.llm_drivers.ollama_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.AsyncClient.return_value = mock_client
            mock_client.chat = AsyncMock(return_value=raw)

            driver = OllamaDriver(settings)
            req = LlmRequest(messages=[LlmMessage(role="user", content="hi")])
            resp = await driver.generate(req)

        assert resp.provider == ProviderId.OLLAMA
        assert resp.content == "hi there"
        mock_client.chat.assert_called_once()

    async def test_generate_with_tools(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_model="qwen2:0.5b")
        raw = SimpleNamespace(
            message=SimpleNamespace(content="", tool_calls=[]),
            prompt_eval_count=5,
            eval_count=3,
            done_reason="tool_calls",
        )

        with patch("backend.infra.llm_drivers.ollama_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.AsyncClient.return_value = mock_client
            mock_client.chat = AsyncMock(return_value=raw)

            driver = OllamaDriver(settings)
            tool = ToolSpec(name="search", description="Search", parameters={"type": "object"})
            req = LlmRequest(messages=[LlmMessage(role="user", content="search it")], tools=[tool])
            await driver.generate(req)

        call_kwargs = mock_client.chat.call_args[1]
        assert "tools" in call_kwargs

    async def test_generate_with_response_schema(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_model="qwen2:0.5b")
        raw = SimpleNamespace(
            message=SimpleNamespace(content='{"verdict":"ok"}', tool_calls=[]),
            prompt_eval_count=5,
            eval_count=3,
            done_reason="stop",
        )

        with patch("backend.infra.llm_drivers.ollama_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.AsyncClient.return_value = mock_client
            mock_client.chat = AsyncMock(return_value=raw)

            driver = OllamaDriver(settings)
            schema = {"type": "object", "required": ["verdict"]}
            req = LlmRequest(
                messages=[LlmMessage(role="user", content="classify")],
                response_schema=schema,
            )
            await driver.generate(req)

        call_kwargs = mock_client.chat.call_args[1]
        assert call_kwargs["format"] == schema

    async def test_generate_exception_raises_llm_error(self):
        from backend.domain.llm import LlmError
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_model="qwen2:0.5b")

        with patch("backend.infra.llm_drivers.ollama_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.AsyncClient.return_value = mock_client
            mock_client.chat = AsyncMock(side_effect=Exception("connection refused"))

            driver = OllamaDriver(settings)
            req = LlmRequest(messages=[LlmMessage(role="user", content="hi")])
            with pytest.raises(LlmError):
                await driver.generate(req)

    async def test_ping_returns_true_when_list_succeeds(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_model="qwen2:0.5b")

        with patch("backend.infra.llm_drivers.ollama_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.AsyncClient.return_value = mock_client
            mock_client.list = AsyncMock(return_value=[])

            driver = OllamaDriver(settings)
            result = await driver.ping()

        assert result is True

    async def test_ping_returns_false_when_list_raises(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_model="qwen2:0.5b")

        with patch("backend.infra.llm_drivers.ollama_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.AsyncClient.return_value = mock_client
            mock_client.list = AsyncMock(side_effect=Exception("unreachable"))

            driver = OllamaDriver(settings)
            result = await driver.ping()

        assert result is False

    async def test_generate_with_temperature_option(self):
        from backend.infra.config import LlmSettings
        from backend.infra.llm_drivers import OllamaDriver

        settings = LlmSettings(ollama_model="qwen2:0.5b")
        raw = SimpleNamespace(
            message=SimpleNamespace(content="hi", tool_calls=[]),
            prompt_eval_count=2,
            eval_count=1,
            done_reason="stop",
        )

        with patch("backend.infra.llm_drivers.ollama_sdk") as mock_sdk:
            mock_client = MagicMock()
            mock_sdk.AsyncClient.return_value = mock_client
            mock_client.chat = AsyncMock(return_value=raw)

            driver = OllamaDriver(settings)
            req = LlmRequest(
                messages=[LlmMessage(role="user", content="hi")],
                temperature=0.7,
                max_tokens=50,
            )
            await driver.generate(req)

        call_kwargs = mock_client.chat.call_args[1]
        assert call_kwargs["options"]["temperature"] == 0.7
        assert call_kwargs["options"]["num_predict"] == 50
