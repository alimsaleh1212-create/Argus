"""Shared scoring helpers — used by both gate runners and tests/eval/* gate tests.

Pure functions only: no I/O, no network.
"""

from __future__ import annotations


def macro_f1(
    tp_real: int, fp_real: int, fn_real: int,
    tp_noise: int, fp_noise: int, fn_noise: int,
) -> float:
    def _f1(tp: int, fp: int, fn: int) -> float:
        if tp + fp == 0 or tp + fn == 0:
            return 0.0
        precision = tp / (tp + fp)
        recall = tp / (tp + fn)
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    return (_f1(tp_real, fp_real, fn_real) + _f1(tp_noise, fp_noise, fn_noise)) / 2


def hit_at_k(hits: list[bool]) -> float:
    """Fraction of queries where the relevant item appeared in the top-k results."""
    if not hits:
        return 0.0
    return sum(hits) / len(hits)


def mean_reciprocal_rank(ranks: list[int | None]) -> float:
    """MRR over a list of 1-indexed ranks; None = not found."""
    if not ranks:
        return 0.0
    return sum(1.0 / r for r in ranks if r is not None) / len(ranks)


def grounded_rate(labels: list[str]) -> float:
    """Weighted grounded rate: grounded=1, partially_grounded=0.5, ungrounded=0."""
    if not labels:
        return 0.0
    score = sum(
        1.0 if lbl == "grounded" else (0.5 if lbl == "partially_grounded" else 0.0)
        for lbl in labels
    )
    return score / len(labels)


def exact_match_agreement(predicted: list[str], gold: list[str]) -> float:
    """Fraction of exact matches between predicted and gold labels."""
    if not predicted or not gold or len(predicted) != len(gold):
        return 0.0
    return sum(p == g for p, g in zip(predicted, gold, strict=True)) / len(predicted)
