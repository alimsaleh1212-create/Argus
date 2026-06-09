"""Unit tests — EnrichmentSettings validation (T006)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.infra.config import EnrichmentSettings


def test_defaults():
    cfg = EnrichmentSettings()
    assert cfg.advance_min_confidence == 0.6
    assert cfg.resolve_min_confidence == 0.7
    assert cfg.corpus_k == 5
    assert cfg.memory_k == 5
    assert cfg.consult_intel is True
    assert cfg.max_indicators == 5
    assert cfg.max_output_tokens == 768
    assert cfg.temperature == 0.0
    assert cfg.prompt_version == "v1"


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        EnrichmentSettings(unknown_key="bad")  # type: ignore[call-arg]


def test_advance_gt_resolve_fails():
    with pytest.raises(ValidationError):
        EnrichmentSettings(advance_min_confidence=0.8, resolve_min_confidence=0.7)


def test_advance_equal_resolve_ok():
    cfg = EnrichmentSettings(advance_min_confidence=0.7, resolve_min_confidence=0.7)
    assert cfg.advance_min_confidence == cfg.resolve_min_confidence


def test_advance_lt_resolve_ok():
    cfg = EnrichmentSettings(advance_min_confidence=0.5, resolve_min_confidence=0.9)
    assert cfg.advance_min_confidence < cfg.resolve_min_confidence
