"""Offline trainer — `python -m backend.anomaly_train` (SPEC-ml-anomaly-detector #17).

Reads the CERT Insider Threat r6.2 CSV logs (or any compatible CSV set), builds
per-user-day behavioral feature vectors, fits an Isolation Forest with a pinned
seed, and persists a small artifact containing the model + feature_spec +
normalization params.

This is a **build-time/offline** command; it never runs on the request path.
Uses pandas, which is kept in the dev/training dependency group only (R8).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from sklearn.ensemble import IsolationForest

from backend.domain.anomaly import RawLogEvent, parse_window
from backend.infra.logging import configure_logging, get_logger
from backend.services.anomaly import build_windows, featurize

logger = get_logger(__name__)

# Default CERT r6.2 column assumptions. Files are expected in the cert-dir:
#   logon.csv, device.csv, file.csv, email.csv, http.csv
# with at least the columns listed below. Column names can be overridden via CLI.
_DEFAULT_FEATURE_SPEC = [
    "logon_count",
    "device_count",
    "file_count",
    "email_count",
    "http_count",
    "distinct_pc",
    "after_hours_count",
    "removable_copy_count",
    "external_email_count",
    "flagged_http_count",
]


class TrainingError(Exception):
    """Unrecoverable training error (missing data, malformed CSV, etc.)."""


def _to_datetime(value: Any) -> datetime | None:
    """Best-effort parsing of CERT date/time strings."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_cert_csv(cert_dir: Path, filename: str) -> pd.DataFrame:
    """Load one CERT CSV file; missing file returns an empty frame."""
    path = cert_dir / filename
    if not path.exists():
        logger.info("train_csv_missing", file=filename)
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception as exc:
        raise TrainingError(f"failed to read {path}: {exc}") from exc


def _records_from_dataframe(
    df: pd.DataFrame,
    *,
    event_type: str,
    user_col: str = "user",
    pc_col: str = "pc",
    time_col: str = "date",
    extra_fields: dict[str, str] | None = None,
) -> list[RawLogEvent]:
    """Convert a CERT dataframe into RawLogEvent records."""
    if df.empty:
        return []

    records: list[RawLogEvent] = []
    for _idx, row in df.iterrows():
        event_time = _to_datetime(row.get(time_col))
        user = row.get(user_col)
        if event_time is None or user is None:
            continue
        pc = row.get(pc_col)
        fields: dict[str, Any] = {"type": event_type}
        if pc is not None and not pd.isna(pc):
            fields["pc"] = str(pc)
        if extra_fields:
            for field_key, col_name in extra_fields.items():
                val = row.get(col_name)
                if val is not None and not pd.isna(val):
                    fields[field_key] = bool(val)
        records.append(
            RawLogEvent(
                event_time=event_time,
                entity_id=str(user),
                fields=fields,
            )
        )
    return records


def load_cert_events(
    cert_dir: str | Path,
    *,
    user_col: str = "user",
    pc_col: str = "pc",
    time_col: str = "date",
) -> list[RawLogEvent]:
    """Load all CERT CSV files in `cert_dir` and return unified RawLogEvents."""
    cert_path = Path(cert_dir)
    if not cert_path.exists():
        raise TrainingError(f"cert-dir does not exist: {cert_path}")

    events: list[RawLogEvent] = []
    file_map = {
        "logon.csv": ({}, "logon"),
        "device.csv": ({}, "device"),
        "file.csv": ({"to_removable": "to_removable"}, "file"),
        "email.csv": ({"external": "to_external"}, "email"),
        "http.csv": ({"flagged": "flagged"}, "http"),
    }

    for filename, (extra_fields, etype) in file_map.items():
        df = _load_cert_csv(cert_path, filename)
        if df.empty:
            continue
        events.extend(
            _records_from_dataframe(
                df,
                event_type=etype,
                user_col=user_col,
                pc_col=pc_col,
                time_col=time_col,
                extra_fields=extra_fields,
            )
        )

    if not events:
        raise TrainingError(f"no valid events found in {cert_path}")

    events.sort(key=lambda e: e.event_time)
    return events


def train_anomaly_model(
    events: list[RawLogEvent],
    *,
    window: timedelta,
    feature_spec: list[str],
    seed: int,
    contamination: float = 0.05,
) -> dict[str, Any]:
    """Build windows, featurize, fit Isolation Forest, and return the artifact dict."""
    windows = build_windows(events, window)
    if not windows:
        raise TrainingError("no windows built from events")

    vectors = [featurize(w, feature_spec) for w in windows]
    matrix = pd.DataFrame([v.values for v in vectors], columns=feature_spec).to_numpy(
        dtype=float
    )

    model = IsolationForest(
        n_estimators=100,
        random_state=seed,
        contamination=contamination,
    )
    model.fit(matrix)

    # Compute normalization params on the training set.
    negated = -model.score_samples(matrix)
    score_min = float(negated.min())
    score_max = float(negated.max())
    if score_max <= score_min:
        # Degenerate training set — add tiny epsilon to avoid division by zero.
        score_max = score_min + 1e-9

    return {
        "model": model,
        "feature_spec": list(feature_spec),
        "score_min": score_min,
        "score_max": score_max,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m backend.anomaly_train")
    p.add_argument("--cert-dir", required=True, help="Directory containing CERT CSV files.")
    p.add_argument("--out", default="backend/data/anomaly/model.joblib", help="Output artifact path.")
    p.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    p.add_argument("--window", default="1d", help="Per-entity aggregation window (e.g. 1d, 1h).")
    p.add_argument(
        "--contamination",
        type=float,
        default=0.05,
        help="Expected outlier fraction for Isolation Forest.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    window = parse_window(args.window)
    events = load_cert_events(args.cert_dir)
    artifact = train_anomaly_model(
        events,
        window=window,
        feature_spec=_DEFAULT_FEATURE_SPEC,
        seed=args.seed,
        contamination=args.contamination,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, out_path)
    logger.info(
        "anomaly_train_complete",
        out=str(out_path),
        events=len(events),
        feature_spec=artifact["feature_spec"],
        score_min=artifact["score_min"],
        score_max=artifact["score_max"],
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    try:
        sys.exit(main())
    except TrainingError as exc:
        logger.error("anomaly_train_failed", error=str(exc))
        sys.exit(1)
