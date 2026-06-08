"""T006 — TriageSettings validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.infra.config import TriageSettings


def test_defaults():
    cfg = TriageSettings()
    assert cfg.advance_min_confidence == 0.6
    assert cfg.resolve_min_confidence == 0.7
    assert cfg.max_output_tokens == 512
    assert cfg.temperature == 0.0
    assert cfg.prompt_version == "v1"


def test_advance_gt_resolve_fails():
    with pytest.raises((ValidationError, ValueError)):
        TriageSettings(advance_min_confidence=0.8, resolve_min_confidence=0.7)


def test_advance_equal_resolve_ok():
    cfg = TriageSettings(advance_min_confidence=0.7, resolve_min_confidence=0.7)
    assert cfg.advance_min_confidence == cfg.resolve_min_confidence


def test_extra_field_rejected():
    with pytest.raises((ValidationError, TypeError)):
        TriageSettings(unknown_field="x")


def test_custom_values():
    cfg = TriageSettings(advance_min_confidence=0.5, resolve_min_confidence=0.9)
    assert cfg.advance_min_confidence == 0.5
    assert cfg.resolve_min_confidence == 0.9
