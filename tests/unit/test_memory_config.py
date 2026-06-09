"""Unit tests for MemorySettings in backend/infra/config.py — T009."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.infra.config import MemorySettings, Settings


# ── MemorySettings defaults ──────────────────────────────────────────────────

def test_memory_settings_defaults() -> None:
    s = MemorySettings()
    assert s.enabled is True
    assert s.backend == "graphiti"
    assert s.neo4j_uri == "bolt://neo4j:7687"
    assert s.neo4j_vault_path == "secret/memory"
    assert s.retrieval_k == 5
    assert s.retrieval_timeout_s == 5.0
    assert s.gemini_embedding_model == "text-embedding-004"
    assert s.embedder_provider == "gemini"


def test_memory_settings_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        MemorySettings(unknown_field="x")  # type: ignore[call-arg]


def test_memory_settings_backend_graphiti() -> None:
    s = MemorySettings(backend="graphiti")
    assert s.backend == "graphiti"


def test_memory_settings_backend_pgvector() -> None:
    s = MemorySettings(backend="pgvector")
    assert s.backend == "pgvector"


def test_memory_settings_backend_invalid() -> None:
    with pytest.raises(ValidationError):
        MemorySettings(backend="cassandra")  # type: ignore[arg-type]


def test_memory_settings_retrieval_k_positive() -> None:
    with pytest.raises(ValidationError):
        MemorySettings(retrieval_k=0)


def test_memory_settings_retrieval_timeout_positive() -> None:
    with pytest.raises(ValidationError):
        MemorySettings(retrieval_timeout_s=0.0)


# ── Settings.memory vault-path wiring ────────────────────────────────────────

def test_settings_memory_vault_path_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """When memory.enabled=True, the neo4j_vault_path must appear in vault.required_paths."""
    monkeypatch.delenv("SENTINEL__VAULT__REQUIRED_PATHS", raising=False)
    s = Settings()
    assert s.memory.neo4j_vault_path in s.vault.required_paths


def test_settings_memory_vault_path_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SENTINEL__VAULT__REQUIRED_PATHS",
        '["secret/memory","secret/llm","secret/ingest"]',
    )
    s = Settings()
    paths = s.vault.required_paths
    assert paths.count("secret/memory") == 1


def test_settings_memory_disabled_skips_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """When memory.enabled=False the neo4j path is NOT injected."""
    monkeypatch.setenv("SENTINEL__MEMORY__ENABLED", "false")
    monkeypatch.setenv(
        "SENTINEL__VAULT__REQUIRED_PATHS",
        '["secret/llm","secret/ingest"]',
    )
    s = Settings()
    assert "secret/memory" not in s.vault.required_paths
