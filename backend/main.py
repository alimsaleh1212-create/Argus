"""FastAPI application factory — thin wiring only.

Builds settings → configures logging → creates the app with the lifespan →
mounts the aggregated API router. All real behaviour lives in the layers
below (routers / services / agents / repositories / infra).
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.infra.config import Settings, load_settings
from backend.infra.lifespan import sentinel_lifespan
from backend.infra.logging import configure_logging
from backend.routers import api_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.app.log_level)

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
