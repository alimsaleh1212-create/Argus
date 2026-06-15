"""T019 — CLI argument parsing and exit-code contract tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from backend.domain.eval import FreezeVerdict, GateKind, GateResult, RunMode
from backend.eval.__main__ import _print_report, parse_args, verdict_to_exit_code


def test_parse_args_defaults():
    args = parse_args([])
    assert args.mode == RunMode.per_pr
    assert args.providers is None
    assert args.gate is None
    assert args.upload is False
    assert args.out == "-"


def test_parse_args_mode_freeze():
    args = parse_args(["--mode", "freeze"])
    assert args.mode == RunMode.freeze


def test_parse_args_providers_list():
    args = parse_args(["--providers", "gemini,ollama"])
    assert args.providers == ["gemini", "ollama"]


def test_parse_args_single_gate():
    args = parse_args(["--gate", "triage"])
    assert args.gate == "triage"


def test_parse_args_upload_flag():
    args = parse_args(["--upload"])
    assert args.upload is True


def test_parse_args_out_path():
    args = parse_args(["--out", "/tmp/report.json"])
    assert args.out == "/tmp/report.json"


def test_exit_code_certifiable():
    assert verdict_to_exit_code(FreezeVerdict.certifiable) == 0


def test_exit_code_not_certifiable():
    assert verdict_to_exit_code(FreezeVerdict.not_certifiable) == 1


def test_exit_code_incomplete():
    assert verdict_to_exit_code(FreezeVerdict.incomplete) == 3


def test_print_report_does_not_raise(capsys):
    """_print_report outputs a table without crashing for typical report shapes."""
    from backend.domain.eval import EvalReport

    report = EvalReport(
        run_id=str(uuid.uuid4()),
        run_mode=RunMode.per_pr,
        commit_sha="abc12345",
        git_tag=None,
        created_at=datetime.now(UTC),
        providers=["ollama"],
        gate_results=[
            GateResult(
                gate="smoke", kind=GateKind.required, provider=None,
                score=1.0, threshold={"max_unhealthy_services": 0},
                passed=True, blocking=True, evidence="ok",
            ),
            GateResult(
                gate="triage", kind=GateKind.required, provider="ollama",
                score=0.80, threshold={"min_macro_f1": 0.75},
                passed=True, blocking=True, evidence="f1=0.80",
            ),
        ],
        rationale=None,
        verdict=FreezeVerdict.certifiable,
        summary={"passed": 2, "failed": 0, "reported": 0, "unknown": 0},
    )
    _print_report(report)
    out = capsys.readouterr().out
    assert "certifiable" in out.lower()
    assert "smoke" in out
    assert "triage" in out


def test_print_report_with_dict_score(capsys):
    """_print_report handles dict score without raising."""
    from backend.domain.eval import EvalReport

    report = EvalReport(
        run_id=str(uuid.uuid4()),
        run_mode=RunMode.freeze,
        commit_sha="def456",
        created_at=datetime.now(UTC),
        providers=["gemini"],
        gate_results=[
            GateResult(
                gate="rationale", kind=GateKind.reported_only, provider=None,
                score={"overall_grounded_rate": 0.80, "overall_judge_agreement": 0.75},
                threshold={"min_grounded_rate": 0.70},
                passed=True, blocking=False, evidence="all stages ok",
            ),
        ],
        verdict=FreezeVerdict.certifiable,
        summary={"passed": 1, "failed": 0, "reported": 0, "unknown": 0},
    )
    _print_report(report)
    out = capsys.readouterr().out
    assert "rationale" in out
