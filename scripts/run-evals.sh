#!/usr/bin/env bash
# Memory-safe batched eval runner.
#
# Runs each gate in its OWN subprocess via `python -m backend.eval --gate <name>`.
# Heavy imports (Presidio/spaCy, graphiti) are reclaimed by the OS when each
# subprocess exits, so peak memory ≈ one gate — mirrors run-tests.sh.
#
# Usage:
#   scripts/run-evals.sh                   # per-PR mode (Ollama only)
#   scripts/run-evals.sh --mode nightly    # nightly mode
#   scripts/run-evals.sh --mode freeze     # freeze mode (both providers + upload)
#
# Any extra args are forwarded to each `python -m backend.eval --gate <name>` call.
set -uo pipefail
cd "$(dirname "$0")/.."

# Forward remaining args to each gate invocation
EXTRA_ARGS=("$@")

COV="${COV:-0}"
declare -a RUNNER=(uv run python)
if [[ "$COV" == "1" ]]; then
  export COVERAGE_FILE="${COVERAGE_FILE:-.coverage.eval}"
  rm -f "${COVERAGE_FILE}" "${COVERAGE_FILE}".* 2>/dev/null || true
  RUNNER=(uv run coverage run -p)
fi

# Discover declared gate names from the yaml (single source of truth)
mapfile -t GATES < <(
  uv run python - <<'PYEOF'
import sys, pathlib
sys.path.insert(0, ".")
from backend.eval.thresholds import load_specs
for s in load_specs():
    print(s.name)
PYEOF
)

if [[ ${#GATES[@]} -eq 0 ]]; then
  echo "ERROR: no gates found in config/eval_thresholds.yaml" >&2
  exit 2
fi

declare -a failed=()
total=${#GATES[@]}

echo "Running ${total} eval gates (one subprocess per gate — OOM-safe)"
echo "────────────────────────────────────────────────────────────────"

for gate in "${GATES[@]}"; do
  echo "▶ gate: $gate"
  "${RUNNER[@]}" -m backend.eval --gate "$gate" "${EXTRA_ARGS[@]}"
  rc=$?
  if [[ $rc -ne 0 ]]; then
    failed+=("$gate (exit $rc)")
  fi
done

if [[ "$COV" == "1" ]]; then
  uv run coverage combine    # merges .coverage.eval.* parallel files -> $COVERAGE_FILE
fi

echo "────────────────────────────────────────────────────────────────"
if (( ${#failed[@]} )); then
  echo "✗ FAILED (${#failed[@]} gate(s)):"
  printf '   %s\n' "${failed[@]}"
  exit 1
fi
echo "✓ all ${total} gates passed"
