#!/usr/bin/env bash
# The AR gate: every check of the scan/AR path that runs on Linux (no Mac, no device).
# Green here is the loop's exit criterion. Each tests/swift/*/run.sh is picked up automatically.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
for run in tests/swift/*/run.sh; do
  [ -e "$run" ] || continue
  echo "== $run"
  bash "$run" 2>&1 | tail -25 || { echo "FAIL $run"; fail=1; }
done
for sim in scripts/ar_sim*.py; do
  [ -e "$sim" ] || continue
  echo "== $sim"
  PYTHONPATH=. python3 "$sim" || { echo "FAIL $sim"; fail=1; }
done
[ $fail -eq 0 ] && echo "AR GATE: GREEN" || echo "AR GATE: RED"
exit $fail
