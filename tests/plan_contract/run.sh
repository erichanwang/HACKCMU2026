#!/usr/bin/env bash
# The plan contract: the document server/planner.py really emits, decoded by the real
# Swift PlanLoader.plan(fromServerDocument:) — the two ends checked against each other
# instead of separately. Deliberately not under tests/swift/, so the AR gate's glob does
# not pick it up; scripts/pipeline_check.sh calls it.
set -euo pipefail
cd "$(dirname "$0")/../.."
if ! command -v swift >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

out="${TMPDIR:-/tmp}/packar-plan-contract"
mkdir -p "$out"

echo "placements: $(python3 tests/plan_contract/make_plan.py "$out/plan_doc.json")"

# SwiftUI does not exist on Linux, so PackingPlanUI cannot compile: --target builds the
# pure-geometry library only. --scratch-path keeps the build out of packing-core/.
swift build --package-path packing-core --scratch-path "$out/build" --target PackingPlan
# The library target leaves objects, not an archive; link them straight into the checker.
# -Onone: top-level code under -O trips a spurious exclusivity check (tests/swift/plan/run.sh).
swiftc -Onone -I "$out/build/debug/Modules" -o "$out/check" \
    tests/plan_contract/main.swift "$out"/build/debug/PackingPlan.build/*.swift.o

"$out/check" "$out/plan_doc.json" "$out/item_dims.json"
