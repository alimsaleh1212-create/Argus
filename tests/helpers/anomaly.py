"""Test helpers for the ML anomaly detector (#17).

`FakeAnomalyModel` implements the `AnomalyModel` Protocol without importing
scikit-learn or touching the real artifact. Unit/integration/e2e tiers inject
this fake; only the `anomaly_detection` eval gate loads the real model.
"""

from __future__ import annotations

from backend.domain.anomaly import FeatureVector


class FakeAnomalyModel:
    """Deterministic, controllable stand-in for `AnomalyModel`.

    - If `scores` contains the vector's `entity_id`, that value is used.
    - Otherwise a deterministic pseudo-score is derived from the vector values:
      `clamp(sum(values) / scale, 0.0, 1.0)`.

    This lets tests craft windows that are clearly anomalous (large values),
    clearly normal (near-zero values), or pin exact scores per entity.
    """

    def __init__(
        self,
        feature_spec: list[str] | None = None,
        scale: float = 100.0,
        scores: dict[str, float] | None = None,
    ):
        self.feature_spec = list(feature_spec) if feature_spec is not None else []
        self.scale = scale
        self.scores = dict(scores) if scores is not None else {}

    def score(self, vectors: list[FeatureVector]) -> list[float]:
        results: list[float] = []
        for vec in vectors:
            if vec.entity_id in self.scores:
                results.append(max(0.0, min(1.0, self.scores[vec.entity_id])))
                continue
            total = sum(vec.values)
            raw = total / self.scale if self.scale else 0.0
            results.append(max(0.0, min(1.0, raw)))
        return results
