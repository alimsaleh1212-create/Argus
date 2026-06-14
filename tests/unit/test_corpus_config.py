"""Unit tests for CorpusSettings + IntelSettings — T007."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.infra.config import CorpusSettings, IntelSettings, Settings

# ── CorpusSettings ───────────────────────────────────────────────────────────


def test_corpus_settings_defaults() -> None:
    s = CorpusSettings()
    assert s.enabled is True
    assert s.data_dir == "backend/data/corpus"
    assert s.retrieval_k == 5


def test_corpus_settings_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        CorpusSettings(unknown_field="x")  # type: ignore[call-arg]


def test_corpus_retrieval_k_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        CorpusSettings(retrieval_k=0)


# ── IntelSettings ────────────────────────────────────────────────────────────


def test_intel_settings_defaults() -> None:
    s = IntelSettings()
    assert s.enabled is False
    assert s.source_name == "demo-intel"
    assert s.base_url == ""
    assert s.api_key_vault_path == "secret/intel"
    assert s.timeout_s == 5.0
    assert s.cache_ttl_s == 3600


def test_intel_settings_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        IntelSettings(unknown_field="x")  # type: ignore[call-arg]


def test_intel_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        IntelSettings(timeout_s=0.0)


def test_intel_cache_ttl_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        IntelSettings(cache_ttl_s=0)


# ── Settings integration — intel key NOT injected into vault.required_paths ─


def test_intel_vault_path_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The intel api_key_vault_path must NOT be force-added to vault.required_paths.

    Contrast with memory.neo4j_vault_path which IS injected.
    """
    monkeypatch.delenv("ARGUS__VAULT__REQUIRED_PATHS", raising=False)
    s = Settings()
    # The intel vault path is optional — must NOT appear unless explicitly added
    assert s.intel.api_key_vault_path not in s.vault.required_paths


def test_memory_vault_path_is_injected_but_not_intel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARGUS__VAULT__REQUIRED_PATHS", raising=False)
    s = Settings()
    assert s.memory.neo4j_vault_path in s.vault.required_paths
    assert s.intel.api_key_vault_path not in s.vault.required_paths
