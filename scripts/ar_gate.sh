#!/usr/bin/env bash
# The AR gate: every check of the scan/AR path that runs on Linux (no Mac, no device).
# Green here is the loop's exit criterion. Each tests/swift/*/run.sh is picked up automatically.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
names=() results=() seconds=()

run_check() {
  local name="$1" cmd="$2" start end
  echo "== $name"
  start=$(date +%s.%N)
  bash -c "$cmd" 2>&1 | tail -25
  local status=${PIPESTATUS[0]}
  end=$(date +%s.%N)
  names+=("$name")
  seconds+=("$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.1f", b-a}')")
  if [ "$status" -eq 0 ]; then
    results+=("PASS")
  else
    results+=("FAIL")
    fail=1
  fi
}

for run in tests/swift/*/run.sh; do
  [ -e "$run" ] || continue
  run_check "$run" "bash '$run'"
done
for sim in scripts/ar_sim*.py; do
  [ -e "$sim" ] || continue
  run_check "$sim" "PYTHONPATH=. python3 '$sim'"
done

echo ""
printf "%-40s %-6s %8s\n" "CHECK" "RESULT" "SECONDS"
for i in "${!names[@]}"; do
  printf "%-40s %-6s %8s\n" "${names[$i]}" "${results[$i]}" "${seconds[$i]}"
done
echo ""
[ $fail -eq 0 ] && echo "AR GATE: GREEN" || echo "AR GATE: RED"
exit $fail
