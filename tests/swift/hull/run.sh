#!/usr/bin/env bash
# LiDAR hull footprints (Spike/Geometry.swift's decimateHull / ScannedItem.footprint), checked
# on Linux (no device, no LiDAR). Non-zero exit = a check failed.
set -euo pipefail
cd "$(dirname "$0")/../../.."
if ! command -v swiftc >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

out="${TMPDIR:-/tmp}/packar-hull-sim"
# -Onone: top-level code under -O trips a spurious exclusivity check.
swiftc -Onone -o "$out" Spike/Geometry.swift tests/swift/SimdShim.swift tests/swift/hull/main.swift
"$out"
