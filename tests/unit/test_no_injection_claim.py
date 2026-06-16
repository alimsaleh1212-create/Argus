"""T044 — Guard: no red_team/injection gate or injection-coverage claim in v1.

FR-015 / Constitution III VD1 defers red-team / prompt-injection testing to v3b.
This test asserts that no such gate is declared in the yaml or registered in
the code, so a future contributor cannot accidentally add one without removing
this guard (which forces a conscious decision).

Checks:
1. eval_thresholds.yaml has no gate named 'red_team' or 'injection*'
2. GATE_REGISTRY has no such key after all gate modules are imported
3. No Python file under backend/eval/ claims injection coverage
   (i.e. no string 'injection' appears as a gate name or test claim)
"""

from __future__ import annotations

import pathlib
import re


def test_no_injection_gate_in_yaml():
    """eval_thresholds.yaml must not declare a red_team or injection gate."""
    import yaml

    with open("config/eval_thresholds.yaml") as f:
        data = yaml.safe_load(f)

    gates = data.get("gates", {})
    for gate_name in gates:
        assert not re.match(r"red_team|injection", gate_name, re.IGNORECASE), (
            f"Gate '{gate_name}' looks like a red-team/injection gate. "
            "VD1 defers injection testing to v3b — remove this gate or update VD1 first."
        )


def test_no_injection_runner_in_registry():
    """GATE_REGISTRY must not contain a red_team or injection runner."""
    import backend.eval.gates.deterministic  # noqa: F401
    import backend.eval.gates.llm  # noqa: F401
    import backend.eval.gates.rationale  # noqa: F401
    import backend.eval.gates.smoke  # noqa: F401
    from backend.eval.gates import GATE_REGISTRY

    for gate_name in GATE_REGISTRY:
        assert not re.match(r"red_team|injection", gate_name, re.IGNORECASE), (
            f"Runner '{gate_name}' looks like a red-team/injection runner. "
            "VD1 defers injection testing to v3b — remove this runner or update VD1 first."
        )


def test_no_injection_coverage_claim_in_eval_code():
    """No file under backend/eval/ claims to provide injection coverage.

    Specifically: no file should contain the string 'injection' as a gate name
    or as a claimed test category. Documentary mentions are excluded by requiring
    the word to appear adjacent to 'gate' or 'coverage'.
    """
    eval_dir = pathlib.Path("backend/eval")
    pattern = re.compile(r"\binjection\s+(gate|coverage|test)\b", re.IGNORECASE)

    matches: list[str] = []
    for py_file in eval_dir.rglob("*.py"):
        text = py_file.read_text()
        if pattern.search(text):
            matches.append(str(py_file))

    assert not matches, (
        "Files claim injection gate/coverage — VD1 defers this to v3b:\n" + "\n".join(matches)
    )
