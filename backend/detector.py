"""One-shot detector runner — `python -m backend.detector` (SPEC-detector #14).

Loads the YAML rule set, replays raw events from a JSON file, evaluates
them through the pure `services.detector.evaluate()`, maps each
`FiredAlert` to a `WazuhAlert`, and emits through the existing
`intake.accept(source=...)` seam (FR-006 / research D1).

Mirrors the closure-factory DI + one-shot-command pattern of #8
(`seed_corpus`). The runner is the only I/O seam; everything below
`evaluate()` is pure.

Exit codes:
  0 — success (including benign-only: zero alerts, zero incidents).
  1 — unrecoverable error (e.g. Postgres unreachable).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.infra.config import DetectorSettings, load_settings
from backend.infra.logging import configure_logging, get_logger
from backend.infra.redaction import build_redactor
from backend.services.detector import (
    evaluate,
    fired_alert_to_wazuh_alert,
    load_replay_events,
    load_rules,
)

logger = get_logger(__name__)


# The DI seam: the runner is built by `make_detector_runner(deps)` and
# returns a closure that performs one run. This mirrors the pattern of
# `seed_corpus.make_seed_corpus_runner` (used by the worker / compose) and
# keeps the I/O surface injectable in tests.
DetectorRunner = Callable[[DetectorSettings], Awaitable[int]]
QueueLike = Any
CacheLike = Any
RedactorLike = Any


def make_detector_runner(
    *,
    settings: Any,
    session_factory: Any,
    queue: QueueLike,
    cache: CacheLike,
    redactor: RedactorLike,
) -> DetectorRunner:
    """Build a closure that runs the detector against `DetectorSettings`.

    All I/O collaborators (session factory, queue, cache, redactor) are
    injected — the runner itself only orchestrates pure functions plus
    `intake.accept`. Honoring `enabled=False` short-circuits to 0.
    """

    async def _run(detector_cfg: DetectorSettings) -> int:
        if not detector_cfg.enabled:
            logger.info("detector_disabled")
            return 0

        rules = load_rules(detector_cfg.rules_path)
        if not detector_cfg.replay_path:
            logger.info("detector_no_replay_path", rules_count=len(rules.rules))
            return 0

        events = load_replay_events(detector_cfg.replay_path)
        if len(events) > detector_cfg.max_events:
            logger.warning(
                "detector_replay_truncated",
                limit=detector_cfg.max_events,
                actual=len(events),
            )
            events = events[: detector_cfg.max_events]
        if not events:
            logger.info("detector_no_replay_events", path=detector_cfg.replay_path)
            return 0

        fired = evaluate(events, rules)
        if not fired:
            logger.info("detector_no_alerts", events=len(events))
            return 0

        from backend.services.intake import accept

        emitted = 0
        async with session_factory() as session:
            for f in fired:
                alert = fired_alert_to_wazuh_alert(f)
                try:
                    await accept(
                        session=session,
                        queue=queue,
                        cache=cache,
                        redactor=redactor,
                        settings=settings,
                        alert=alert,
                        source=detector_cfg.source_tag,
                    )
                    emitted += 1
                except Exception as exc:
                    # One bad alert must not sink the whole run.
                    logger.warning(
                        "detector_emit_failed",
                        rule=f.rule_id,
                        error=str(exc),
                    )

        logger.info(
            "detector_run_complete",
            events=len(events),
            fired=len(fired),
            emitted=emitted,
        )
        return 0

    return _run


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m backend.detector")
    p.add_argument("--rules", default=None, help="Path to the rule set YAML.")
    p.add_argument("--replay", default=None, help="Path to the replay events JSON.")
    return p.parse_args(argv)


async def _main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    settings = load_settings()
    detector_cfg: DetectorSettings = settings.detector
    if args.rules:
        detector_cfg = detector_cfg.model_copy(update={"rules_path": args.rules})
    if args.replay:
        detector_cfg = detector_cfg.model_copy(update={"replay_path": args.replay})

    if not Path(detector_cfg.rules_path).exists():
        logger.info("detector_no_rules", path=detector_cfg.rules_path)
        return 0
    if not detector_cfg.replay_path or not Path(detector_cfg.replay_path).exists():
        logger.info("detector_no_replay", path=str(detector_cfg.replay_path))
        return 0

    # Wire I/O collaborators
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
                runner = make_detector_runner(
                    settings=settings,
                    session_factory=factory,
                    queue=queue_client,
                    cache=cache_client,
                    redactor=redactor,
                )
                try:
                    return await runner(detector_cfg)
                except Exception as exc:
                    logger.error("detector_run_failed", error=str(exc))
                    return 1
    finally:
        await engine.dispose()


def main() -> None:  # pragma: no cover
    sys.exit(asyncio.run(_main()))


if __name__ == "__main__":  # pragma: no cover
    main()
