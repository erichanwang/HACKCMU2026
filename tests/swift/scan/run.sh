#!/usr/bin/env bash
# Simulates the AR scanner's geometry on Linux. Non-zero exit = a check failed.
set -euo pipefail
cd "$(dirname "$0")/../../.."
source swift/PackPhysics/swiftenv.sh
out="${TMPDIR:-/tmp}/packar-scan-sim"
# -Onone: top-level code under -O trips a spurious exclusivity check in Swift 6.3.
swiftc -Onone Spike/Geometry.swift tests/swift/SimdShim.swift tests/swift/scan/*.swift -o "$out"
"$out"
