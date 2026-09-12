#!/usr/bin/env bash
# Scan geometry (Spike/Geometry.swift) on Linux, via the simd shim.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
source "$root/swift/PackPhysics/swiftenv.sh"
export SWIFT_BACKTRACE=enable=no
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
swiftc -Onone -o "$out/scan" "$root/Spike/Geometry.swift" "$root/tests/swift/SimdShim.swift" "$root/tests/main.swift"
"$out/scan"
