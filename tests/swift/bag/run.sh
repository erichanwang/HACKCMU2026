#!/usr/bin/env bash
# Flat-wall vs calibrated per-axis interior model, checked on Linux (no device, no LiDAR).
set -euo pipefail
cd "$(dirname "$0")/../../.."
if ! command -v swiftc >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

out="${TMPDIR:-/tmp}/packar-bag-sim"
# -Onone: top-level code under -O trips a spurious exclusivity check.
swiftc -Onone -o "$out" Spike/Geometry.swift Spike/PlanAnchor.swift tests/swift/SimdShim.swift tests/swift/bag/*.swift
"$out"
