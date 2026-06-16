"""Entry point: python -m backend.eval

Flags:
  --mode {per_pr,nightly,freeze}   selects provider set + whether report uploads
  --providers a,b                  override provider list
  --gate NAME                      run one gate (used by the memory-safe runner)
  --upload                         persist report to MinIO
  --out PATH                       also write eval_report.json locally (- = skip)

Exit codes:
  0  all required gates passed (no catastrophic-floor breach)
  1  required gate failed or reported-only gate breached catastrophic floor
  2  orphan/stale gate mismatch (abort before scoring)
  3  incomplete: required dimension could not evaluate or MinIO upload failed
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
from collections.abc import Sequence

from backend.domain.eval import FreezeVerdict, RunMode


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="python -m backend.eval", description="Argus eval harness")
    p.add_argument(
        "--mode",
        choices=[m.value for m in RunMode],
        default=RunMode.per_pr.value,
        type=lambda s: RunMode(s),
        help="run mode (per_pr|nightly|freeze). Selects provider set.",
    )
    p.add_argument(
        "--providers",
        default=None,
        type=lambda s: [x.strip() for x in s.split(",")],
        help="comma-separated provider list, overrides mode default",
    )
    p.add_argument("--gate", default=None, help="run a single gate by name")
    p.add_argument("--upload", action="store_true", help="persist report to MinIO")
    p.add_argument("--out", default="-", help="local file path for report JSON (- = skip)")
    return p.parse_args(argv)


def verdict_to_exit_code(verdict: FreezeVerdict) -> int:
    if verdict == FreezeVerdict.certifiable:
        return 0
    if verdict == FreezeVerdict.not_certifiable:
        return 1
    if verdict == FreezeVerdict.incomplete:
        return 3
    return 1  # safe default


def _print_report(report, *, redact: bool = True) -> None:
    """Print a human-readable per-gate summary. Redacts evidence if requested."""

    print(f"\nEval Report — {report.run_mode.value} | commit={report.commit_sha[:8]}")
    print(f"Providers: {', '.join(report.providers)}")
    print(f"{'Gate':<30} {'Provider':<10} {'Score':<12} {'Threshold':<12} {'Kind':<14} {'Status'}")
    print("-" * 90)
    for r in report.gate_results:
        score_str = (
            f"{r.score:.3f}"
            if isinstance(r.score, float)
            else ", ".join(f"{k}={v:.3f}" for k, v in r.score.items())
        )
        provider_str = r.provider or "-"
        status = "PASS" if r.passed else ("UNKNOWN" if r.passed is None else "FAIL")
        kind_str = r.kind.value
        threshold_str = str(r.threshold)[:12]
        print(
            f"  {r.gate:<28} {provider_str:<10} {score_str:<12} {threshold_str:<12} {kind_str:<14} {status}"
        )
    if report.rationale:
        print("\nRationale scores:")
        for rs in report.rationale:
            print(
                f"  {rs.stage}/{rs.producer_provider}: "
                f"grounded={rs.grounded_rate:.2f} agreement={rs.judge_human_agreement:.2f} n={rs.n}"
            )
    print(f"\nVerdict: {report.verdict.value.upper()}")
    summary = report.summary
    print(
        f"Summary: passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} "
        f"reported={summary.get('reported', 0)} unknown={summary.get('unknown', 0)}"
    )


async def _run(args: argparse.Namespace) -> int:  # pragma: no cover
    # Import gate modules to register runners (side-effect)
    import backend.eval.gates.deterministic  # noqa: F401
    import backend.eval.gates.feedback  # noqa: F401
    import backend.eval.gates.llm  # noqa: F401
    import backend.eval.gates.rationale  # noqa: F401
    import backend.eval.gates.smoke  # noqa: F401
    import backend.eval.gates.supervisor_routing  # noqa: F401
    import backend.eval.gates.temporal_memory  # noqa: F401
    import backend.eval.gates.verification  # noqa: F401
    from backend.eval.gates import RegistryMismatchError, validate_registry
    from backend.eval.thresholds import load_specs
    from backend.infra.config import EvalSettings

    cfg = EvalSettings()
    specs = load_specs(cfg.thresholds_path)

    # Orphan/stale guard
    try:
        validate_registry(specs)
    except RegistryMismatchError as e:
        print(f"ERROR: gate registry mismatch: {e}", file=sys.stderr)
        return 2

    # Single-gate mode (used by run-evals.sh)
    if args.gate:
        specs = [s for s in specs if s.name == args.gate]
        if not specs:
            print(f"ERROR: gate '{args.gate}' not declared in yaml", file=sys.stderr)
            return 2
        # Also restrict registry to avoid orphan/stale error on subset
        from backend.eval.gates import GATE_REGISTRY

        registry = {s.name: GATE_REGISTRY[s.name] for s in specs if s.name in GATE_REGISTRY}
    else:
        from backend.eval.gates import GATE_REGISTRY

        registry = GATE_REGISTRY

    # Determine provider set
    if args.providers:
        providers = args.providers
    elif args.mode in (RunMode.freeze, RunMode.nightly):
        providers = cfg.providers_freeze
    else:
        providers = cfg.providers_per_pr

    # Get commit sha
    try:
        commit_sha = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"], text=True
        ).strip()
    except Exception:
        commit_sha = os.environ.get("GITHUB_SHA", "unknown")[:12]

    git_tag: str | None = None
    if args.mode == RunMode.freeze:
        try:
            git_tag = subprocess.check_output(
                ["git", "describe", "--tags", "--exact-match"], text=True
            ).strip()
        except Exception:
            git_tag = os.environ.get("GITHUB_REF_NAME")

    # Run rationale gate only at freeze/nightly
    run_rationale = args.mode in (RunMode.freeze, RunMode.nightly)
    if not run_rationale and args.gate is None:
        # Filter out reported-only gates that only run at freeze/nightly
        specs_filtered: list = []
        for s in specs:
            if s.kind.value == "reported_only" and s.name == "rationale":
                continue
            specs_filtered.append(s)
        specs = specs_filtered
        registry = {s.name: registry[s.name] for s in specs if s.name in registry}

    from backend.eval.harness import run_harness

    report = await run_harness(
        specs,
        registry,
        run_mode=args.mode,
        providers=providers,
        commit_sha=commit_sha,
        git_tag=git_tag,
    )

    _print_report(report)

    # Write local file if requested
    if args.out != "-":
        from pathlib import Path

        Path(args.out).write_text(report.model_dump_json(indent=2))

    # Upload to MinIO if requested
    if args.upload or args.mode == RunMode.freeze:
        try:
            from backend.eval.report import upload_report

            await upload_report(report, cfg)
        except Exception as e:
            print(f"WARNING: MinIO upload failed: {e}", file=sys.stderr)
            from backend.domain.eval import FreezeVerdict as V

            if report.verdict == V.certifiable:
                report = report.model_copy(update={"verdict": V.incomplete})

    return verdict_to_exit_code(report.verdict)


def main() -> None:  # pragma: no cover
    args = parse_args()
    code = asyncio.run(_run(args))
    sys.exit(code)


if __name__ == "__main__":  # pragma: no cover
    main()
