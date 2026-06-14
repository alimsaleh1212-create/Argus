#!/usr/bin/env bash
# Memory-safe batched test runner.
#
# Runs each test file in its OWN pytest subprocess. Heavy imports that are never
# released within a process — spaCy `en_core_web_lg` (via Presidio redaction),
# graphiti_core — are reclaimed by the OS when each subprocess exits, so peak
# memory ≈ one file rather than the whole suite. This is why the full suite OOMs
# in a single `pytest tests/...` process on memory-constrained machines (or while
# the `docker compose` stack is running) but runs fine here.
#
# Usage:
#   scripts/run-tests.sh unit              # fast unit tier (no Docker)
#   scripts/run-tests.sh e2e               # in-process e2e (skips needs_compose)
#   scripts/run-tests.sh integration       # testcontainers tier (Docker)
#
# Env:
#   COV=1     collect coverage (appends to .coverage; caller resets/reports)
#   BATCH=N   files per subprocess (default 1 — lowest peak memory)
set -uo pipefail
cd "$(dirname "$0")/.."

TIER="${1:-unit}"
COV="${COV:-0}"
BATCH="${BATCH:-1}"

declare -a MARK=()
case "$TIER" in
  unit)        mapfile -t FILES < <(ls tests/unit/test_*.py) ;;
  integration) mapfile -t FILES < <(ls tests/integration/test_*.py) ;;
  e2e)         mapfile -t FILES < <(ls tests/e2e/test_*.py); MARK=(-m "not needs_compose") ;;
  *) echo "usage: $0 {unit|integration|e2e}" >&2; exit 2 ;;
esac

declare -a COV_ARGS=()
[[ "$COV" == "1" ]] && COV_ARGS=(--cov=backend --cov-append --cov-report=)

declare -a failed=()
total=${#FILES[@]}
i=0
while (( i < total )); do
  batch=("${FILES[@]:i:BATCH}")
  i=$(( i + BATCH ))
  echo "▶ ${batch[*]}"
  uv run pytest "${batch[@]}" "${MARK[@]}" "${COV_ARGS[@]}" -p no:cacheprovider -q
  rc=$?
  # exit 5 = "no tests collected" (e.g. a file fully deselected by a marker) → OK
  if [[ $rc -ne 0 && $rc -ne 5 ]]; then
    failed+=("${batch[*]}")
  fi
done

echo "──────────────────────────────────────────"
if (( ${#failed[@]} )); then
  echo "✗ FAILED (${#failed[@]} batch(es)):"
  printf '   %s\n' "${failed[@]}"
  exit 1
fi
echo "✓ all ${total} files passed (${TIER} tier)"
