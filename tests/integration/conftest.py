"""Integration test fixtures."""

from __future__ import annotations

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def real_llm_client():
    """Build a real LlmClient (Ollama only) for integration tests.

    Uses a minimal observability bundle; no Vault key required.
    Skips if the Ollama service is not reachable.
    """
    try:
        from backend.domain.llm import ProviderId
        from backend.infra.config import LlmSettings
        from backend.infra.llm import LlmClient
        from backend.infra.llm_drivers import OllamaDriver
        from backend.infra.observability import Observability
        from backend.infra.redaction import build_redactor
        from backend.infra.tracing import build_tracer

        llm_cfg = LlmSettings()
        obs = Observability(
            redactor=build_redactor(presidio_enabled=False),
            tracer=build_tracer(exporter=None),
        )
        drivers = {
            ProviderId.OLLAMA: OllamaDriver(llm_cfg),
        }
        client = LlmClient(settings=llm_cfg, drivers=drivers, obs=obs)

        # Quick reachability check — skip if Ollama is down
        try:
            driver = drivers[ProviderId.OLLAMA]
            reachable = await driver.ping()
            if not reachable:
                pytest.skip("Ollama not reachable — skip real_llm_client fixture")
        except Exception:
            pytest.skip("Ollama ping failed — skip real_llm_client fixture")

        yield client
    except Exception as exc:
        pytest.skip(f"real_llm_client fixture unavailable: {exc}")
