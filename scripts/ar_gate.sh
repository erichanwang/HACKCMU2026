#!/usr/bin/env bash
# The AR gate: every check of the scan/AR path that runs on Linux (no Mac, no device).
# Green here is the loop's exit criterion. Each tests/swift/*/run.sh is picked up automatically.
set -uo pipefail
cd "$(dirname "$0")/.."
fail=0
names=() results=() seconds=() details=()
# Checks print "AR-WARN: <name> | <detail>" for a characterised limitation that is real but not
# a failure. They are collected here and shown as WARN rows, so a GREEN gate never implies
# there is nothing to know -- see ar_sim.py's warn().
warn_names=() warn_details=() warn_seen=()

run_check() {
  local name="$1" cmd="$2" start end out line
  echo "== $name"
  start=$(date +%s.%N)
  out=$(bash -c "$cmd" 2>&1)
  local status=$?
  printf '%s\n' "$out" | tail -25
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    # A check may emit the same warning once per variant it exercises; report it once.
    case " ${warn_seen[*]-} " in *" $line "*) continue ;; esac
    warn_seen+=("$line")
    warn_names+=("${line%% | *}")
    warn_details+=("${line#* | }")
  done < <(printf '%s\n' "$out" | sed -n 's/^AR-WARN: //p')
  end=$(date +%s.%N)
  names+=("$name")
  seconds+=("$(awk -v a="$start" -v b="$end" 'BEGIN{printf "%.1f", b-a}')")
  details+=("")
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
  # --adversarial: the clutter-merge and drift sweeps never fail the gate, but without the
  # flag they never RUN either, so those two characterised limitations had no automated
  # coverage at all and their WARN emitters could rot unnoticed. Costs a few seconds.
  run_check "$sim" "PYTHONPATH=. python3 '$sim' --adversarial"
done

echo ""
printf "%-40s %-6s %8s  %s\n" "CHECK" "RESULT" "SECONDS" "DETAIL"
for i in "${!names[@]}"; do
  printf "%-40s %-6s %8s  %s\n" "${names[$i]}" "${results[$i]}" "${seconds[$i]}" "${details[$i]}"
done
for i in "${!warn_names[@]}"; do
  printf "%-40s %-6s %8s  %s\n" "${warn_names[$i]}" "WARN" "" "${warn_details[$i]}"
done
echo ""
[ $fail -eq 0 ] && echo "AR GATE: GREEN" || echo "AR GATE: RED"
exit $fail
