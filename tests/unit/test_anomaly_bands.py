"""Unit tests — T020: score_to_severity bands + fire_threshold (US2).

Covers:
- Below fire_threshold → no fire.
- Band breakpoints map to the correct Severity.
- Score clamping at band boundaries.
"""

from __future__ import annotations

from backend.domain.anomaly import ScoreBands
from backend.domain.incident import Severity
from backend.services.anomaly import score_to_severity


class TestScoreToSeverity:
    def test_below_fire_threshold_returns_none(self) -> None:
        bands = ScoreBands(fire_threshold=0.60)
        assert score_to_severity(0.59, bands) is None
        assert score_to_severity(0.0, bands) is None

    def test_exact_fire_threshold_fires(self) -> None:
        bands = ScoreBands(fire_threshold=0.60)
        assert score_to_severity(0.60, bands) is Severity.MEDIUM

    def test_band_breakpoints(self) -> None:
        bands = ScoreBands(
            fire_threshold=0.60,
            band_medium=0.60,
            band_high=0.75,
            band_critical=0.90,
        )
        assert score_to_severity(0.60, bands) is Severity.MEDIUM
        assert score_to_severity(0.74, bands) is Severity.MEDIUM
        assert score_to_severity(0.75, bands) is Severity.HIGH
        assert score_to_severity(0.89, bands) is Severity.HIGH
        assert score_to_severity(0.90, bands) is Severity.CRITICAL
        assert score_to_severity(1.0, bands) is Severity.CRITICAL

    def test_custom_bands(self) -> None:
        bands = ScoreBands(
            fire_threshold=0.50,
            band_medium=0.50,
            band_high=0.70,
            band_critical=0.85,
        )
        assert score_to_severity(0.49, bands) is None
        assert score_to_severity(0.50, bands) is Severity.MEDIUM
        assert score_to_severity(0.69, bands) is Severity.MEDIUM
        assert score_to_severity(0.70, bands) is Severity.HIGH
