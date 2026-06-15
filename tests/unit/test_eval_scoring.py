"""Coverage for backend/eval/gates/scoring.py pure math helpers."""

from __future__ import annotations

from backend.eval.gates.scoring import (
    exact_match_agreement,
    grounded_rate,
    hit_at_k,
    macro_f1,
    mean_reciprocal_rank,
)

# ── macro_f1 ──────────────────────────────────────────────────────────────────


def test_macro_f1_perfect():
    # 5 real TPs, 5 noise TPs, no FP/FN
    result = macro_f1(tp_real=5, fp_real=0, fn_real=0, tp_noise=5, fp_noise=0, fn_noise=0)
    assert result == 1.0


def test_macro_f1_zero_predictions():
    # All TPs=0 → both classes get F1=0
    result = macro_f1(tp_real=0, fp_real=0, fn_real=5, tp_noise=0, fp_noise=0, fn_noise=5)
    assert result == 0.0


def test_macro_f1_zero_tp_with_fp():
    # No correct predictions, some false positives → precision undefined → F1=0
    result = macro_f1(tp_real=0, fp_real=3, fn_real=3, tp_noise=0, fp_noise=2, fn_noise=2)
    assert result == 0.0


def test_macro_f1_partial():
    # 3/4 precision, 3/4 recall for real; same for noise
    # F1 = 2 * 0.75 * 0.75 / (0.75 + 0.75) = 0.75
    result = macro_f1(tp_real=3, fp_real=1, fn_real=1, tp_noise=3, fp_noise=1, fn_noise=1)
    assert abs(result - 0.75) < 1e-9


def test_macro_f1_asymmetric():
    # real: perfect F1=1.0; noise: F1=0 (no TPs)
    result = macro_f1(tp_real=3, fp_real=0, fn_real=0, tp_noise=0, fp_noise=0, fn_noise=3)
    assert abs(result - 0.5) < 1e-9


# ── hit_at_k ──────────────────────────────────────────────────────────────────


def test_hit_at_k_all_hits():
    assert hit_at_k([True, True, True]) == 1.0


def test_hit_at_k_no_hits():
    assert hit_at_k([False, False, False]) == 0.0


def test_hit_at_k_partial():
    assert abs(hit_at_k([True, False, True, False]) - 0.5) < 1e-9


def test_hit_at_k_empty():
    assert hit_at_k([]) == 0.0


# ── mean_reciprocal_rank ──────────────────────────────────────────────────────


def test_mrr_first_position():
    # rank 1 → 1/1 = 1.0
    assert mean_reciprocal_rank([1]) == 1.0


def test_mrr_second_position():
    assert abs(mean_reciprocal_rank([2]) - 0.5) < 1e-9


def test_mrr_mixed():
    # (1/1 + 1/2 + 1/3) / 3 = (1 + 0.5 + 0.333...) / 3 = 1.833.../3
    result = mean_reciprocal_rank([1, 2, 3])
    expected = (1.0 + 0.5 + 1 / 3) / 3
    assert abs(result - expected) < 1e-9


def test_mrr_with_not_found():
    # None items counted in denominator but contribute 0 to sum
    result = mean_reciprocal_rank([1, None, 2])
    expected = (1.0 + 0.0 + 0.5) / 3
    assert abs(result - expected) < 1e-9


def test_mrr_empty():
    assert mean_reciprocal_rank([]) == 0.0


# ── grounded_rate edge cases ──────────────────────────────────────────────────


def test_grounded_rate_empty():
    assert grounded_rate([]) == 0.0


def test_grounded_rate_mixed():
    # 2G + 1PG + 2UG = (2 + 0.5) / 5 = 0.50
    result = grounded_rate(["grounded", "grounded", "partially_grounded", "ungrounded", "ungrounded"])
    assert abs(result - 0.50) < 1e-9


# ── exact_match_agreement edge cases ─────────────────────────────────────────


def test_exact_match_empty_predicted():
    assert exact_match_agreement([], ["grounded"]) == 0.0


def test_exact_match_length_mismatch():
    assert exact_match_agreement(["grounded"], ["grounded", "ungrounded"]) == 0.0
