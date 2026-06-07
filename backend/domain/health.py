"""Domain health types — pure data, no outward dependencies."""

from __future__ import annotations

from pydantic import BaseModel


class DependencyStatus(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None  # redaction-safe; never contains secret values


class ReadinessReport(BaseModel):
    ready: bool
    dependencies: list[DependencyStatus]


class Liveness(BaseModel):
    status: str = "ok"
