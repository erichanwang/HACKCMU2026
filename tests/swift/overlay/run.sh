#!/usr/bin/env bash
# PlanOverlayState's pure step/colour/unpacked model, checked on Linux. The RealityKit builder in
# Spike/PlanOverlay.swift only gets a parse check — ARKit/RealityKit don't exist here.
set -euo pipefail

# overlay: Linux-only. Spike/PlanOverlay.swift imports UIKit inside #if canImport(RealityKit);
#   on Linux that block is skipped, on macOS RealityKit exists but UIKit does not. CI runs it on ubuntu.
if [ "$(uname -s)" != "Linux" ]; then
    echo "skip: overlay only runs on Linux (see the note above); CI runs it on ubuntu-24.04"
    exit 0
fi
cd "$(dirname "$0")/../../.."
if ! command -v swift >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

out="${TMPDIR:-/tmp}/packar-overlay-sim"
mkdir -p "$out"

# SwiftUI does not exist on Linux, so PackingPlanUI cannot compile: --target builds the
# pure-geometry library only (same trick as tests/plan_contract/run.sh).
swift build --package-path packing-core --scratch-path "$out/build" --target PackingPlan
# The library target leaves objects, not an archive; link them straight into the checker.
# -Onone: top-level code under -O trips a spurious exclusivity check (tests/swift/plan/run.sh).
swiftc -Onone -I "$out/build/debug/Modules" -o "$out/check" \
    Spike/PlanOverlay.swift tests/swift/overlay/main.swift "$out"/build/debug/PackingPlan.build/*.swift.o
"$out/check"

swiftc -parse Spike/PlanOverlay.swift
echo "PlanOverlay parse — ok"
