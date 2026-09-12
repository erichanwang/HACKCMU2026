#!/usr/bin/env bash
# Open-lid suitcase scans vs. fitBox/rimHeight (Spike/Geometry.swift), checked on Linux (no
# device, no LiDAR). Non-zero exit = a check failed.
set -euo pipefail
cd "$(dirname "$0")/../../.."
if ! command -v swiftc >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

out="${TMPDIR:-/tmp}/packar-lid-sim"
# -Onone: top-level code under -O trips a spurious exclusivity check.
swiftc -Onone -o "$out" Spike/Geometry.swift tests/swift/SimdShim.swift tests/swift/lid/main.swift
"$out"
