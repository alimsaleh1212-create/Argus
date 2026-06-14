"""Unit tests — T007 [US1]: uniform seam, DI, tool-scoping, structured-output.

All tests use faked drivers — zero real provider calls (SC-008).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.domain.llm import (
    LlmError,
    LlmErrorKind,
    LlmMessage,
    LlmRequest,
    LlmResponse,
    ProviderId,
    StopReason,
    TokenUsage,
    ToolCall,
    ToolSpec,
)


def _make_fake_response(
    content: str = "ok",
    provider: ProviderId = ProviderId.GEMINI,
    model: str = "gemini-1.5-flash",
    tool_calls: list | None = None,
) -> LlmResponse:
    return LlmResponse(
        content=content,
        tool_calls=tool_calls or [],
        usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
        model=model,
        provider=provider,
        stop_reason=StopReason.END_TURN,
    )


def _make_client(
    fake_response: LlmResponse | None = None,
    raise_error: LlmError | None = None,
) -> Any:
    """Build an LlmClient with both drivers replaced by AsyncMocks."""
    from backend.infra.config import LlmSettings
    from backend.infra.llm import LlmClient
    from backend.infra.redaction import build_redactor
    from backend.infra.tracing import build_tracer

    settings = LlmSettings()

    # Build a minimal observability-like object
    tracer = build_tracer()
    redactor = build_redactor(presidio_enabled=False)

    class FakeObs:
        pass

    obs = FakeObs()
    obs.tracer = tracer
    obs.redactor = redactor

    # Fake driver
    driver = MagicMock()
    driver.provider_id = ProviderId.GEMINI
    if raise_error:
        driver.generate = AsyncMock(side_effect=raise_error)
    else:
        driver.generate = AsyncMock(return_value=fake_response or _make_fake_response())

    client = LlmClient(
        settings=settings, drivers={ProviderId.GEMINI: driver, ProviderId.OLLAMA: driver}, obs=obs
    )
    return client, driver


class TestUniformSeam:
    async def test_generate_returns_uniform_response(self) -> None:
        """LlmClient.generate() returns LlmResponse regardless of backend (FR-002)."""
        client, _ = _make_client()
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="test-1")
        assert isinstance(resp, LlmResponse)
        assert resp.provider in (ProviderId.GEMINI, ProviderId.OLLAMA)
        assert resp.model
        assert resp.stop_reason

    async def test_response_carries_usage(self) -> None:
        """Response carries normalized TokenUsage with prompt_tokens / completion_tokens."""
        client, _ = _make_client()
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="test-2")
        assert hasattr(resp.usage, "prompt_tokens")
        assert hasattr(resp.usage, "completion_tokens")

    async def test_response_carries_provider_and_model(self) -> None:
        """Response identifies serving provider and concrete model."""
        client, _ = _make_client()
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")])
        resp = await client.generate(req, correlation_id="test-3")
        assert resp.provider in ProviderId
        assert isinstance(resp.model, str) and resp.model


class TestToolScoping:
    async def test_no_tools_no_tool_call_emitted(self) -> None:
        """Client with no permitted tools cannot produce a tool call (FR-003)."""
        # Even if driver mistakenly emits tool calls, the client with empty tools
        # should either forward the call (per spec — scoping is in the request passed
        # to the driver, which has empty tools) or the driver respects it.
        # Here we assert that sending an empty tools list results in no tool calls
        # in the response when none are requested.
        resp_no_tools = _make_fake_response(tool_calls=[])
        client, driver = _make_client(fake_response=resp_no_tools)
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")], tools=[])
        resp = await client.generate(req, correlation_id="test-4")
        # The driver was called with the request that has empty tools
        call_args = driver.generate.call_args[0][0]
        assert call_args.tools == []
        assert resp.tool_calls == []

    async def test_tools_passed_through_unchanged(self) -> None:
        """Only the tools the caller specifies reach the driver (FR-003)."""
        tool = ToolSpec(name="search", description="Search the web", parameters={"type": "object"})
        tc = ToolCall(id="c1", name="search", arguments={"q": "test"})
        resp_with_tool = _make_fake_response(tool_calls=[tc])
        resp_with_tool = resp_with_tool.model_copy(update={"stop_reason": StopReason.TOOL_USE})
        client, driver = _make_client(fake_response=resp_with_tool)
        req = LlmRequest(messages=[LlmMessage(role="user", content="hello")], tools=[tool])
        await client.generate(req, correlation_id="test-5")
        # The request forwarded to the driver should carry the specified tools
        forwarded_req = driver.generate.call_args[0][0]
        assert len(forwarded_req.tools) == 1
        assert forwarded_req.tools[0].name == "search"


class TestStructuredOutput:
    async def test_valid_schema_response_passes(self) -> None:
        """When response_schema is set and content is valid JSON, returns normally."""
        import json

        schema = {
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"],
        }
        valid_content = json.dumps({"verdict": "benign"})
        resp = _make_fake_response(content=valid_content)
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="classify")],
            response_schema=schema,
        )
        result = await client.generate(req, correlation_id="test-6")
        assert result.content == valid_content

    async def test_invalid_schema_response_raises_contract_unsatisfied(self) -> None:
        """When response_schema is set and content fails validation, raises CONTRACT_UNSATISFIED."""
        import json

        schema = {
            "type": "object",
            "properties": {"verdict": {"type": "string"}},
            "required": ["verdict"],
        }
        # Response missing required field
        bad_content = json.dumps({"other": "value"})
        resp = _make_fake_response(content=bad_content)
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="classify")],
            response_schema=schema,
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="test-7")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED

    async def test_non_json_content_raises_contract_unsatisfied(self) -> None:
        """Unparseable JSON in content raises CONTRACT_UNSATISFIED (not valid JSON)."""
        schema = {"type": "object", "required": ["verdict"]}
        resp = _make_fake_response(content="not-json-at-all{{{")
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="classify")],
            response_schema=schema,
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="test-8")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED

    async def test_non_dict_json_raises_contract_unsatisfied(self) -> None:
        """JSON array in content (not an object) raises CONTRACT_UNSATISFIED."""
        import json

        schema = {"type": "object", "required": ["verdict"]}
        resp = _make_fake_response(content=json.dumps([1, 2, 3]))
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="classify")],
            response_schema=schema,
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="test-9")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED

    async def test_require_tool_specific_name_satisfied(self) -> None:
        """require_tool=<name> passes when a matching tool call is present."""
        tool = ToolSpec(name="alert", description="Raise an alert", parameters={"type": "object"})
        tc = ToolCall(id="c1", name="alert", arguments={"level": "high"})
        resp = _make_fake_response(tool_calls=[tc])
        resp = resp.model_copy(update={"stop_reason": StopReason.TOOL_USE})
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="alert?")],
            tools=[tool],
            require_tool="alert",
        )
        result = await client.generate(req, correlation_id="test-10")
        assert result.tool_calls[0].name == "alert"

    async def test_require_tool_wrong_name_raises_contract_unsatisfied(self) -> None:
        """require_tool=<name> raises CONTRACT_UNSATISFIED when a different tool call is returned."""
        tool = ToolSpec(name="search", description="Search", parameters={"type": "object"})
        tc = ToolCall(id="c1", name="search", arguments={})
        resp = _make_fake_response(tool_calls=[tc])
        resp = resp.model_copy(update={"stop_reason": StopReason.TOOL_USE})
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="must alert")],
            tools=[tool],
            require_tool="alert",  # not in response
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="test-11")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED

    async def test_require_tool_no_calls_raises_contract_unsatisfied(self) -> None:
        """require_tool set but response has no tool calls → CONTRACT_UNSATISFIED."""
        resp = _make_fake_response(tool_calls=[])
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="must use tool")],
            require_tool="alert",
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="test-12")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED

    async def test_tool_call_non_dict_arguments_raises_contract_unsatisfied(self) -> None:
        """Tool call arguments that are not a dict → CONTRACT_UNSATISFIED (fail-closed)."""
        tc = ToolCall(id="c1", name="alert", arguments={"level": "high"})
        resp = _make_fake_response(tool_calls=[tc])
        resp = resp.model_copy(update={"stop_reason": StopReason.TOOL_USE})
        # Patch arguments to be non-dict (bypass Pydantic by using object)
        object.__setattr__(tc, "arguments", "not-a-dict")
        client, _ = _make_client(fake_response=resp)
        req = LlmRequest(
            messages=[LlmMessage(role="user", content="must use tool")],
            require_tool=True,
        )
        with pytest.raises(LlmError) as exc_info:
            await client.generate(req, correlation_id="test-13")
        assert exc_info.value.kind == LlmErrorKind.CONTRACT_UNSATISFIED


class TestNoBypasGuard:
    def test_vendor_sdks_only_in_llm_drivers(self) -> None:
        """google-genai and ollama are imported ONLY in backend/infra/llm_drivers.py (FR-001/SC-001)."""
        import ast
        import os

        forbidden_importers: list[str] = []
        backend_root = os.path.join(os.path.dirname(__file__), "..", "..", "backend")
        for dirpath, _dirs, files in os.walk(backend_root):
            for fname in files:
                if not fname.endswith(".py"):
                    continue
                fpath = os.path.join(dirpath, fname)
                # Normalise to repo-relative
                rel = os.path.relpath(fpath, os.path.join(backend_root, ".."))
                if rel == os.path.join("backend", "infra", "llm_drivers.py"):
                    continue  # allowed
                try:
                    tree = ast.parse(open(fpath).read())
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    if isinstance(node, (ast.Import, ast.ImportFrom)):
                        names = []
                        if isinstance(node, ast.Import):
                            names = [alias.name for alias in node.names]
                        elif node.module:
                            names = [node.module]
                        for name in names:
                            if name.startswith(("google.genai", "google-genai", "ollama")):
                                forbidden_importers.append(f"{rel}: {name}")

        assert forbidden_importers == [], (
            "Vendor SDK imported outside llm_drivers.py:\n" + "\n".join(forbidden_importers)
        )
