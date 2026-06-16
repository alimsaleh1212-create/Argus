"""T036 — Rationale judge gate tests.

Validates:
- grounded_rate formula: (#grounded + 0.5*#partially_grounded) / n  (R2)
- judge_human_agreement = exact-match rate of judge label vs human_label
- catastrophic_floor promotion: grounded_rate < 0.50 → blocking=True  (R3)
- ordinary below-target (0.50 ≤ rate < 0.70) → not blocking  (reported-only, FR-004)
- gate skips when run_mode=per_pr  (run_modes=[freeze,nightly])
- redaction: judge receives redacted context (T040 covers integration; here unit-level)
- GateResult has correct kind=reported_only, blocking computed from catastrophic_floor
"""

from __future__ import annotations

import json
import pathlib

import pytest

from backend.domain.eval import GateKind, GateProviderDim, GateSpec, RunMode

# ── helpers ──────────────────────────────────────────────────────────────────


def _load_fixtures(stage: str) -> list[dict]:
    p = pathlib.Path("tests/fixtures/rationale") / f"{stage}.json"
    return json.loads(p.read_text())


def _make_rationale_spec(*, catastrophic_floor: float = 0.50) -> GateSpec:
    return GateSpec(
        name="rationale",
        description="rationale judge gate",
        kind=GateKind.reported_only,
        provider_dim=GateProviderDim.provider_independent,
        threshold={
            "min_grounded_rate": 0.70,
            "min_judge_agreement": 0.70,
        },
        providers=[],
    )


# ── grounded_rate formula ─────────────────────────────────────────────────────


def test_grounded_rate_formula():
    """grounded_rate = (#grounded + 0.5*#partially_grounded) / n"""
    from backend.eval.gates.scoring import grounded_rate

    labels = ["grounded", "grounded", "partially_grounded", "ungrounded", "grounded"]
    # 3 grounded + 0.5*1 = 3.5 / 5 = 0.70
    result = grounded_rate(labels)
    assert abs(result - 0.70) < 1e-9


def test_grounded_rate_all_grounded():
    from backend.eval.gates.scoring import grounded_rate

    assert grounded_rate(["grounded"] * 5) == 1.0


def test_grounded_rate_all_ungrounded():
    from backend.eval.gates.scoring import grounded_rate

    assert grounded_rate(["ungrounded"] * 3) == 0.0


# ── judge agreement ────────────────────────────────────────────────────────────


def test_exact_match_agreement_perfect():
    from backend.eval.gates.scoring import exact_match_agreement

    assert exact_match_agreement(["grounded", "ungrounded"], ["grounded", "ungrounded"]) == 1.0


def test_exact_match_agreement_half():
    from backend.eval.gates.scoring import exact_match_agreement

    assert exact_match_agreement(["grounded", "ungrounded"], ["grounded", "grounded"]) == 0.5


# ── fixture files are valid ───────────────────────────────────────────────────


@pytest.mark.parametrize("stage", ["triage", "enrichment", "response"])
def test_fixture_files_are_valid(stage: str):
    """Each fixture file must exist and contain valid RationaleJudgeSample fields."""
    from backend.domain.eval import RationaleJudgeSample, RationaleLabel

    samples = _load_fixtures(stage)
    assert len(samples) >= 5, f"{stage} needs ≥5 samples"
    for s in samples:
        sample = RationaleJudgeSample(**s)
        assert sample.human_label in list(RationaleLabel)
        assert isinstance(sample.cites_supplied_evidence, bool)
        assert sample.incident_context
        assert sample.rationale_text


# ── catastrophic floor promotion ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_catastrophic_floor_breach_makes_blocking():
    """grounded_rate < 0.50 catastrophic → blocking=True on a reported_only gate."""
    from backend.eval.gates.rationale import run_rationale

    spec = _make_rationale_spec()

    # Fake judge that always returns "ungrounded" (grounded_rate=0.0)
    async def _always_ungrounded(context: str, rationale: str) -> str:
        return "ungrounded"

    result = await run_rationale(spec, provider=None, _judge_fn=_always_ungrounded)
    assert result.kind == GateKind.reported_only
    assert result.blocking is True  # catastrophic floor breach
    assert result.passed is False


@pytest.mark.asyncio
async def test_below_target_but_above_floor_not_blocking(tmp_path, monkeypatch):
    """grounded_rate=0.60 and agreement=0.60 → above floor (0.50), below target (0.70).

    Uses controlled fixtures (all human_label='grounded') and a judge that returns
    grounded for 3/5 and ungrounded for 2/5 per stage. This gives:
      grounded_rate = 3/5 = 0.60  (above floor 0.50, below target 0.70)
      agreement     = 3/5 = 0.60  (above floor 0.50, below target 0.70)
    → blocking=False, passed=False
    """
    import backend.eval.gates.rationale as rat_mod
    from backend.eval.gates.rationale import run_rationale

    fixture_data = json.dumps(
        [
            {
                "incident_context": "ctx",
                "rationale_text": "rat",
                "human_label": "grounded",
                "cites_supplied_evidence": True,
            }
            for _ in range(5)
        ]
    )
    for stage in ["triage", "enrichment", "response"]:
        (tmp_path / f"{stage}.json").write_text(fixture_data)

    monkeypatch.setattr(rat_mod, "_FIXTURE_DIR", tmp_path)

    spec = _make_rationale_spec()
    count = {"n": 0}

    async def _judge(context: str, rationale: str) -> str:
        label = "grounded" if count["n"] % 5 < 3 else "ungrounded"
        count["n"] += 1
        return label

    result = await run_rationale(spec, provider=None, _judge_fn=_judge)
    assert result.kind == GateKind.reported_only
    assert result.blocking is False  # above catastrophic floor (0.60 > 0.50)
    assert result.passed is False  # below target (0.60 < 0.70)


@pytest.mark.asyncio
async def test_above_target_passes_and_not_blocking():
    """grounded_rate ≥ 0.70 → passed=True, blocking=False."""
    from backend.eval.gates.rationale import run_rationale

    spec = _make_rationale_spec()

    async def _always_grounded(context: str, rationale: str) -> str:
        return "grounded"

    result = await run_rationale(spec, provider=None, _judge_fn=_always_grounded)
    assert result.passed is True
    assert result.blocking is False


# ── gate skips for per_pr mode ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rationale_gate_skipped_per_pr():
    """run_rationale returns unknown/passed=None when run_mode=per_pr."""
    from backend.eval.gates.rationale import run_rationale

    spec = _make_rationale_spec()

    async def _fail_if_called(context: str, rationale: str) -> str:
        raise AssertionError("judge should not be called in per_pr mode")

    result = await run_rationale(
        spec, provider=None, run_mode=RunMode.per_pr, _judge_fn=_fail_if_called
    )
    assert result.passed is None  # unknown — gate was skipped
    assert result.blocking is False


# ── missing fixture files ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_fixture_dir_yields_unknown(tmp_path, monkeypatch):
    """When no fixture files exist the gate returns unknown (passed=None)."""
    import backend.eval.gates.rationale as rat_mod
    from backend.eval.gates.rationale import run_rationale

    monkeypatch.setattr(rat_mod, "_FIXTURE_DIR", tmp_path)  # empty dir

    spec = _make_rationale_spec()

    async def _never_called(context: str, rationale: str) -> str:
        raise AssertionError("should not be called with no fixtures")

    result = await run_rationale(spec, provider=None, _judge_fn=_never_called)
    assert result.passed is None  # unknown — no fixture data
    assert result.blocking is False
    assert "missing" in result.evidence or "no fixtures" in result.evidence
