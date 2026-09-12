#!/usr/bin/env bash
# Plan -> AR world transform, checked on Linux. The ARKit files that use it cannot compile here,
# so they only get a parse check; the math they call is all in the pure files compiled below.
set -euo pipefail
cd "$(dirname "$0")/../../.."
if ! command -v swiftc >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

out="${TMPDIR:-/tmp}/packar-plan-sim"
# -Onone: top-level code under -O trips a spurious exclusivity check.
swiftc -Onone -o "$out" Spike/Geometry.swift Spike/PlanAnchor.swift tests/swift/SimdShim.swift tests/swift/plan/*.swift
"$out"
swiftc -parse Spike/ScanView.swift Spike/SpikeApp.swift Spike/API.swift
echo "ScanView/SpikeApp/API parse — ok"
