"""Unit tests for deterministic feedback bias rules (US2, T009)."""

from __future__ import annotations

from backend.domain.feedback import (
    FeedbackSignal,
    RemediationOutcome,
    decide_severity_bias,
    has_prior_failure,
    prefer_stronger_playbook,
)
from backend.domain.incident import Severity
from backend.infra.config import FeedbackSettings


class _SimpleObj:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _signal(outcome: RemediationOutcome, current: bool = True) -> FeedbackSignal:
    return FeedbackSignal(
        indicator="10.0.0.1", outcome=outcome, is_current=current, observed_at=None
    )


class TestHasPriorFailure:
    def test_true_on_failure_class(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.REGRESSED)]
        assert has_prior_failure(signals, cfg) is True

    def test_true_on_unverified(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.UNVERIFIED)]
        assert has_prior_failure(signals, cfg) is True

    def test_false_on_verified(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.VERIFIED)]
        assert has_prior_failure(signals, cfg) is False

    def test_false_when_not_current(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.REGRESSED, current=False)]
        assert has_prior_failure(signals, cfg) is False

    def test_false_when_empty(self) -> None:
        cfg = FeedbackSettings()
        assert has_prior_failure([], cfg) is False

    def test_respects_escalate_on_config(self) -> None:
        cfg = FeedbackSettings(escalate_on=["regressed"])
        signals = [_signal(RemediationOutcome.UNVERIFIED)]
        assert has_prior_failure(signals, cfg) is False


class TestDecideSeverityBias:
    def test_bump_one_from_medium_to_high(self) -> None:
        cfg = FeedbackSettings(severity_bias="bump_one")
        signals = [_signal(RemediationOutcome.REGRESSED)]
        assert decide_severity_bias(Severity.MEDIUM, signals, cfg) == Severity.HIGH

    def test_bump_one_caps_at_critical(self) -> None:
        cfg = FeedbackSettings(severity_bias="bump_one")
        signals = [_signal(RemediationOutcome.REGRESSED)]
        assert decide_severity_bias(Severity.CRITICAL, signals, cfg) == Severity.CRITICAL

    def test_to_critical(self) -> None:
        cfg = FeedbackSettings(severity_bias="to_critical")
        signals = [_signal(RemediationOutcome.REGRESSED)]
        assert decide_severity_bias(Severity.LOW, signals, cfg) == Severity.CRITICAL

    def test_none_mode(self) -> None:
        cfg = FeedbackSettings(severity_bias="none")
        signals = [_signal(RemediationOutcome.REGRESSED)]
        assert decide_severity_bias(Severity.MEDIUM, signals, cfg) == Severity.MEDIUM

    def test_no_bias_when_no_failure_signal(self) -> None:
        cfg = FeedbackSettings(severity_bias="bump_one")
        signals = [_signal(RemediationOutcome.VERIFIED)]
        assert decide_severity_bias(Severity.MEDIUM, signals, cfg) == Severity.MEDIUM

    def test_deterministic_repeat(self) -> None:
        cfg = FeedbackSettings(severity_bias="bump_one")
        signals = [_signal(RemediationOutcome.REGRESSED)]
        a = decide_severity_bias(Severity.MEDIUM, signals, cfg)
        b = decide_severity_bias(Severity.MEDIUM, signals, cfg)
        assert a == b == Severity.HIGH


class FakePlaybook:
    def __init__(self, pb_id: str, strength: int = 0):
        self.id = pb_id
        self.strength = strength


class TestPreferStrongerPlaybook:
    def test_picks_highest_strength_on_failure(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.REGRESSED)]
        candidates = [FakePlaybook("weak", 1), FakePlaybook("strong", 3)]
        chosen = prefer_stronger_playbook(candidates, signals, cfg)
        assert chosen is not None
        assert chosen.id == "strong"

    def test_no_change_when_no_failure(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.VERIFIED)]
        candidates = [FakePlaybook("weak", 1), FakePlaybook("strong", 3)]
        assert prefer_stronger_playbook(candidates, signals, cfg) is None

    def test_no_change_when_disabled(self) -> None:
        cfg = FeedbackSettings(prefer_stronger_playbook=False)
        signals = [_signal(RemediationOutcome.REGRESSED)]
        candidates = [FakePlaybook("weak", 1), FakePlaybook("strong", 3)]
        assert prefer_stronger_playbook(candidates, signals, cfg) is None

    def test_no_change_when_all_tie(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.REGRESSED)]
        candidates = [FakePlaybook("a", 2), FakePlaybook("b", 2)]
        assert prefer_stronger_playbook(candidates, signals, cfg) is None

    def test_no_change_with_single_candidate(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.REGRESSED)]
        candidates = [FakePlaybook("only", 5)]
        assert prefer_stronger_playbook(candidates, signals, cfg) is None

    def test_default_strength_zero(self) -> None:
        cfg = FeedbackSettings()
        signals = [_signal(RemediationOutcome.REGRESSED)]
        candidates = [FakePlaybook("default"), FakePlaybook("strong", 1)]
        chosen = prefer_stronger_playbook(candidates, signals, cfg)
        assert chosen is not None
        assert chosen.id == "strong"
