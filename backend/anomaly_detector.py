"""One-shot anomaly detector runner — `python -m backend.anomaly_detector` (#17).

Loads the saved Isolation Forest artifact, replays raw SIEM logs, scores each
per-entity window, maps findings over the fire threshold to WazuhAlerts, and
emits through the existing `intake.accept(source=...)` seam.

Mirrors the closure-factory DI + one-shot-command pattern of `backend/detector.py`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.domain.anomaly import AnomalyModel
from backend.infra.anomaly_model import ModelArtifactError, SklearnAnomalyModel
from backend.infra.config import AnomalySettings, load_settings
from backend.infra.logging import configure_logging, get_logger
from backend.infra.redaction import build_redactor
from backend.services.anomaly import (
    build_windows,
    featurize,
    finding_to_wazuh_alert,
    load_replay_events,
    score_to_severity,
)

logger = get_logger(__name__)

AnomalyRunner = Callable[[AnomalySettings], Awaitable[int]]
QueueLike = Any
CacheLike = Any
RedactorLike = Any


def make_anomaly_runner(
    *,
    settings: Any,
    session_factory: Any,
    queue: QueueLike,
    cache: CacheLike,
    redactor: RedactorLike,
    model: AnomalyModel,
) -> AnomalyRunner:
    """Build a closure that runs the anomaly detector against `AnomalySettings`.

    All I/O collaborators are injected. The runner orchestrates pure functions
    plus `intake.accept`. Honoring `enabled=False` short-circuits to 0.
    """

    async def _run(anomaly_cfg: AnomalySettings) -> int:
        if not anomaly_cfg.enabled:
            logger.info("anomaly_detector_disabled")
            return 0

        if not anomaly_cfg.replay_path:
            logger.info("anomaly_detector_no_replay_path")
            return 0

        events = load_replay_events(anomaly_cfg.replay_path)
        if len(events) > anomaly_cfg.max_events:
            logger.warning(
                "anomaly_replay_truncated",
                limit=anomaly_cfg.max_events,
                actual=len(events),
            )
            events = events[: anomaly_cfg.max_events]
        if not events:
            logger.info("anomaly_detector_no_replay_events", path=anomaly_cfg.replay_path)
            return 0

        windows = build_windows(events, anomaly_cfg.window)
        if not windows:
            logger.info("anomaly_detector_no_windows")
            return 0

        vectors = [featurize(w, model.feature_spec) for w in windows]
        scores = model.score(vectors)
        bands = anomaly_cfg.score_bands

        findings = []
        for window, score in zip(windows, scores, strict=True):
            severity = score_to_severity(score, bands)
            if severity is None:
                continue
            from backend.domain.anomaly import AnomalyFinding

            findings.append(
                AnomalyFinding(
                    entity_id=window.entity_id,
                    score=score,
                    severity=severity,
                    window=window,
                    top_features=_top_features(window),
                )
            )

        if not findings:
            logger.info("anomaly_detector_no_alerts", windows=len(windows))
            return 0

        from backend.services.intake import accept

        emitted = 0
        async with session_factory() as session:
            for finding in findings:
                alert = finding_to_wazuh_alert(finding)
                try:
                    await accept(
                        session=session,
                        queue=queue,
                        cache=cache,
                        redactor=redactor,
                        settings=settings,
                        alert=alert,
                        source=anomaly_cfg.source_tag,
                    )
                    emitted += 1
                except Exception as exc:
                    logger.warning(
                        "anomaly_emit_failed",
                        entity=finding.entity_id,
                        error=str(exc),
                    )

        logger.info(
            "anomaly_detector_run_complete",
            windows=len(windows),
            findings=len(findings),
            emitted=emitted,
        )
        return 0

    return _run


def _top_features(window: Any, n: int = 3) -> list[str]:
    """Return the n highest-valued feature names as evidence."""
    if not window.features:
        return []
    sorted_items = sorted(
        window.features.items(),
        key=lambda item: item[1],
        reverse=True,
    )
    return [name for name, _ in sorted_items[:n]]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m backend.anomaly_detector")
    p.add_argument("--model", default=None, help="Path to the saved model artifact.")
    p.add_argument("--replay", default=None, help="Path to the replay JSON/JSONL.")
    return p.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    settings = load_settings()
    anomaly_cfg: AnomalySettings = settings.anomaly
    if args.model:
        anomaly_cfg = anomaly_cfg.model_copy(update={"model_path": args.model})
    if args.replay:
        anomaly_cfg = anomaly_cfg.model_copy(update={"replay_path": args.replay})

    # Fail-closed on missing/unloadable model (FR-012)
    try:
        model = SklearnAnomalyModel(anomaly_cfg.model_path)
    except ModelArtifactError as exc:
        logger.error("anomaly_model_load_failed", error=str(exc))
        return 1

    if not anomaly_cfg.replay_path or not Path(anomaly_cfg.replay_path).exists():
        logger.info("anomaly_no_replay", path=str(anomaly_cfg.replay_path))
        return 0

    dsn = settings.postgres.dsn.get_secret_value()
    engine = create_async_engine(dsn, pool_pre_ping=True, echo=False)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)
    try:
        from backend.infra.cache import CacheProvider

        cache_provider = CacheProvider()
        async with cache_provider.build(settings) as cache_client:
            from backend.infra.queue import QueueProvider

            queue_provider = QueueProvider()
            async with queue_provider.build(settings) as queue_client:
                redactor = build_redactor(settings.observability)
                runner = make_anomaly_runner(
                    settings=settings,
                    session_factory=factory,
                    queue=queue_client,
                    cache=cache_client,
                    redactor=redactor,
                    model=model,
                )
                try:
                    return await runner(anomaly_cfg)
                except Exception as exc:
                    logger.error("anomaly_detector_run_failed", error=str(exc))
                    return 1
    finally:
        await engine.dispose()


def main() -> None:  # pragma: no cover
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":  # pragma: no cover
    main()
