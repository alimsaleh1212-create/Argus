"""FastAPI application factory — thin wiring only.

Builds settings → registers providers → configures logging → creates the app
with the lifespan → mounts the aggregated API router.
All real behaviour lives in the layers below (routers / services / infra).
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.infra.config import Settings, load_settings
from backend.infra.lifespan import sentinel_lifespan
from backend.infra.logging import configure_logging
from backend.routers import api_router


def _bootstrap_providers() -> None:
    """Register all infra providers in startup order (vault → db → blob → obs → llm).

    Guards against double-registration so this is safe to call multiple times
    (subsequent calls from tests that manage their own registry are fine — they
    call clear_registry() before registering their own providers).
    """
    from backend.infra.container import get_registry, register_provider
    from backend.infra.vault import register_vault_provider
    from backend.infra.db import register_db_provider
    from backend.infra.blob import register_blob_provider
    from backend.infra.observability import ObservabilityProvider
    from backend.infra.llm import register_llm_provider

    existing_names = {p.name for p in get_registry()}
    if "vault_client" not in existing_names:
        register_vault_provider()
    if "db_engine" not in existing_names:
        register_db_provider()
    if "blob_client" not in existing_names:
        register_blob_provider()
    if "observability" not in existing_names:
        register_provider(ObservabilityProvider())
    if "llm" not in existing_names:
        register_llm_provider()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.app.log_level)
    _bootstrap_providers()

    app = FastAPI(
        title="Sentinel",
        description="AI-driven SOAR platform",
        version="0.1.0",
        lifespan=sentinel_lifespan,
    )
    app.state.settings = settings
    app.include_router(api_router)
    return app


app = create_app()
