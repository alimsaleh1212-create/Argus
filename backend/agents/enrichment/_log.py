"""Internal logger factory for the enrichment package.

Falls back to the stdlib logger if the structlog seam is unavailable at import time
(degraded boot) — mirrors the pattern used by the other agent stages.
"""

from __future__ import annotations

from typing import Any


def get_logger(name: str) -> Any:
    try:
        from backend.infra.logging import get_logger as _get_logger

        return _get_logger(name)
    except Exception:
        import logging

        return logging.getLogger(name)
