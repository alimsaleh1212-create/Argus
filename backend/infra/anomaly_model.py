"""Real anomaly model wrapper — owns scikit-learn/joblib (SPEC-ml-anomaly-detector #17).

`SklearnAnomalyModel` loads the committed `model.joblib` artifact and exposes the
pure `AnomalyModel` Protocol. This is the **only** production path that imports
scikit-learn; unit/integration/e2e tests inject `FakeAnomalyModel` instead.

The artifact contains:
- fitted `IsolationForest`
- ordered `feature_spec`
- normalization params (`score_min`, `score_max`) for negated score_samples

Scoring: `score_samples` → negate → min-max-normalize with saved params → [0,1].
Fail-closed on missing/unloadable artifact (FR-012).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from backend.domain.anomaly import FeatureVector

logger = logging.getLogger(__name__)


class ModelArtifactError(Exception):
    """Raised when the saved artifact cannot be loaded or is malformed."""


class SklearnAnomalyModel:
    """Concrete `AnomalyModel` backed by a saved Isolation Forest artifact."""

    def __init__(self, model_path: str | Path):
        self.model_path = Path(model_path)
        artifact = self._load_artifact(self.model_path)
        self.model: IsolationForest = artifact["model"]
        self.feature_spec: list[str] = list(artifact["feature_spec"])
        self.score_min: float = float(artifact["score_min"])
        self.score_max: float = float(artifact["score_max"])

        if self.score_max <= self.score_min:
            raise ModelArtifactError(
                f"invalid normalization params: score_max ({self.score_max}) "
                f"must be greater than score_min ({self.score_min})"
            )

    def _load_artifact(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            raise ModelArtifactError(f"model artifact not found: {path}")
        try:
            artifact = joblib.load(path)
        except Exception as exc:
            raise ModelArtifactError(f"failed to load model artifact {path}: {exc}") from exc

        required_keys = {"model", "feature_spec", "score_min", "score_max"}
        missing = required_keys - set(artifact.keys())
        if missing:
            raise ModelArtifactError(f"artifact missing keys: {sorted(missing)}")

        if not isinstance(artifact["model"], IsolationForest):
            raise ModelArtifactError("artifact 'model' is not an IsolationForest")

        return artifact

    def score(self, vectors: list[FeatureVector]) -> list[float]:
        """Return a [0,1] anomaly score for each vector.

        Higher = more anomalous. Uses the saved normalization params so replay
        scoring reproduces training-time scaling exactly.
        """
        if not vectors:
            return []

        matrix = np.array([v.values for v in vectors], dtype=np.float64)
        # score_samples: higher = more normal; negate so higher = more anomalous.
        negated = -self.model.score_samples(matrix)
        # Min-max normalize using saved training-time params.
        denom = self.score_max - self.score_min
        if denom == 0:
            normalized = np.zeros_like(negated)
        else:
            normalized = (negated - self.score_min) / denom
        # Clamp to [0,1] to protect against tiny floating-point drift.
        return [float(max(0.0, min(1.0, s))) for s in normalized]
