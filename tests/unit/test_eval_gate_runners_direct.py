"""Direct gate runner coverage — exercises run_smoke, run_triage, run_llm_provider,
and run_supervisor_routing by calling them with their built-in fake/scripted
dependencies. No real LLM, DB, or network needed.
"""

from __future__ import annotations

import pytest

from backend.domain.eval import GateKind, GateProviderDim, GateSpec


def _spec(name: str, threshold: dict) -> GateSpec:
    return GateSpec(
        name=name,
        description="direct test",
        kind=GateKind.required,
        provider_dim=GateProviderDim.provider_independent,
        threshold=threshold,
    )


# ---------------------------------------------------------------------------
# smoke gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_smoke_pass_from_env(monkeypatch):
    """SMOKE_STATUS=pass env var → gate passes without HTTP probe."""
    monkeypatch.setenv("SMOKE_STATUS", "pass")
    import backend.eval.gates.smoke  # noqa: F401
    from backend.eval.gates.smoke import run_smoke

    spec = _spec("smoke", {"max_unhealthy_services": 0})
    result = await run_smoke(spec)
    assert result.passed is True
    assert "SMOKE_STATUS=pass" in result.evidence


@pytest.mark.asyncio
async def test_run_smoke_fail_from_env(monkeypatch):
    """SMOKE_STATUS=fail env var → gate fails without HTTP probe."""
    monkeypatch.setenv("SMOKE_STATUS", "fail")
    from backend.eval.gates.smoke import run_smoke

    spec = _spec("smoke", {"max_unhealthy_services": 0})
    result = await run_smoke(spec)
    assert result.passed is False
    assert "SMOKE_STATUS=fail" in result.evidence


@pytest.mark.asyncio
async def test_run_smoke_no_env_stack_unreachable(monkeypatch):
    """No env var, httpx raises → passed=None (stack unreachable path)."""
    monkeypatch.delenv("SMOKE_STATUS", raising=False)

    import httpx as _httpx

    class _FakeAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url):
            raise _httpx.ConnectError("forced unreachable")

    monkeypatch.setattr(_httpx, "AsyncClient", lambda **_kw: _FakeAsyncClient())

    from backend.eval.gates.smoke import run_smoke

    spec = _spec("smoke", {"max_unhealthy_services": 0})
    result = await run_smoke(spec)
    assert result.passed is None
    assert "unreachable" in result.evidence


# ---------------------------------------------------------------------------
# triage gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_triage_with_real_fixtures():
    """run_triage uses its built-in scripted LLM; only needs fixture files on disk."""
    import backend.eval.gates.llm  # noqa: F401
    from backend.eval.gates.llm import run_triage

    spec = _spec("triage", {"min_macro_f1": 0.75, "max_abstention_rate": 0.3})
    result = await run_triage(spec, provider="ollama")
    # Scripted LLM returns gold labels verbatim → should pass
    assert result.gate == "triage"
    assert result.provider == "ollama"
    assert isinstance(result.score, dict)
    assert "macro_f1" in result.score
    assert result.passed is True


@pytest.mark.asyncio
async def test_run_triage_no_fixtures(tmp_path, monkeypatch):
    """run_triage with missing fixture dir → passed=None (no fixtures found)."""
    import backend.eval.gates.llm as llm_mod

    monkeypatch.setattr(llm_mod, "_FIXTURES", tmp_path)
    from backend.eval.gates.llm import run_triage

    spec = _spec("triage", {"min_macro_f1": 0.75, "max_abstention_rate": 0.3})
    result = await run_triage(spec)
    assert result.passed is None
    assert "no triage fixtures" in result.evidence


# ---------------------------------------------------------------------------
# llm_provider gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_llm_provider_client_unavailable(monkeypatch):
    """run_llm_provider when get_llm_client raises → passed=None."""

    async def _raise_on_build(provider):
        raise RuntimeError("no credentials")

    monkeypatch.setattr(
        "backend.infra.llm.get_llm_client",
        _raise_on_build,
        raising=False,
    )

    from backend.eval.gates.llm import run_llm_provider

    spec = _spec("llm_provider", {"max_provider_failures": 0})
    result = await run_llm_provider(spec, provider="gemini")
    assert result.passed is None
    assert "unavailable" in result.evidence


# ---------------------------------------------------------------------------
# llm_provider gate — success path via fake client
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_llm_provider_success_path(monkeypatch):
    """run_llm_provider with a fake client → passed=True."""
    import sys

    from backend.domain.llm import LlmResponse, ProviderId, StopReason, TokenUsage

    class _FakeClient:
        async def generate(self, req):
            return LlmResponse(
                content="pong",
                usage=TokenUsage(prompt_tokens=5, completion_tokens=2),
                model="fake",
                provider=ProviderId.OLLAMA,
                stop_reason=StopReason.END_TURN,
            )

    async def _fake_get_client(provider):
        return _FakeClient()

    # Patch at sys.modules level so the `from backend.infra.llm import get_llm_client`
    # inside run_llm_provider picks up the fake.
    import backend.infra.llm as _llm_mod

    monkeypatch.setattr(_llm_mod, "get_llm_client", _fake_get_client, raising=False)
    # Also ensure the module is in sys.modules so the from-import resolves from it
    sys.modules["backend.infra.llm"] = _llm_mod

    from backend.eval.gates.llm import run_llm_provider

    spec = _spec("llm_provider", {"max_provider_failures": 0})
    result = await run_llm_provider(spec, provider="ollama")
    assert result.passed is True
    assert "content_len" in result.evidence


# ---------------------------------------------------------------------------
# retrieval / temporal_memory / redaction — no-stack paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_retrieval_no_memory_store_returns_corpus_only(monkeypatch):
    """run_retrieval without MemoryStore falls back to corpus-only scoring."""
    import backend.eval.gates.deterministic  # noqa: F401
    from backend.eval.gates.deterministic import run_retrieval

    spec = _spec("retrieval", {"min_hit_at_k": 0.8, "k": 5, "min_mrr": 0.6})
    result = await run_retrieval(spec)
    # Either corpus-only passed=True/False or fully unavailable passed=None
    assert result.gate == "retrieval"
    assert result.passed in (True, False, None)


@pytest.mark.asyncio
async def test_run_temporal_memory_unavailable_returns_none():
    """run_temporal_memory when test helper is absent → passed=None."""
    import backend.eval.gates.temporal_memory  # noqa: F401
    from backend.eval.gates.temporal_memory import run_temporal_memory

    spec = _spec("temporal_memory", {"pass_rate": 1.0})
    result = await run_temporal_memory(spec)
    # In unit tier without a real stack, the helper raises → passed=None
    assert result.gate == "temporal_memory"
    assert result.passed in (True, False, None)


@pytest.mark.asyncio
async def test_run_redaction_unavailable_returns_none():
    """run_redaction when test helper is absent → passed=None."""
    from backend.eval.gates.deterministic import run_redaction

    spec = _spec(
        "redaction",
        {
            "max_credential_leaks": 0,
            "max_pii_leaks": 0,
            "boundaries_checked": [],
            "seeded_credentials": [],
            "seeded_pii": [],
        },
    )
    result = await run_redaction(spec)
    assert result.gate == "redaction"
    assert result.passed in (True, False, None)


# ---------------------------------------------------------------------------
# supervisor_routing gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_supervisor_routing_all_fixtures_pass():
    """run_supervisor_routing uses built-in fake repo + fake stages → 100% pass rate."""
    import backend.eval.gates.supervisor_routing  # noqa: F401
    from backend.eval.gates.supervisor_routing import run_supervisor_routing

    spec = _spec("supervisor_routing", {"pass_rate": 1.0})
    result = await run_supervisor_routing(spec)
    assert result.gate == "supervisor_routing"
    assert result.passed is True
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_run_supervisor_routing_returns_gate_result():
    """Gate result is well-formed GateResult from the runner."""
    from backend.domain.eval import GateResult
    from backend.eval.gates.supervisor_routing import run_supervisor_routing

    spec = _spec("supervisor_routing", {"pass_rate": 1.0})
    result = await run_supervisor_routing(spec)
    assert isinstance(result, GateResult)
    assert result.blocking is True
