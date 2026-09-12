#!/usr/bin/env bash
# How far the AR overlay (Spike/PlanAnchor.swift) lands from truth when every scan input is noisy.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
source "$root/swift/PackPhysics/swiftenv.sh"
export SWIFT_BACKTRACE=enable=no
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
swiftc -Onone -o "$out/drift" "$root/Spike/Geometry.swift" "$root/Spike/PlanAnchor.swift" \
  "$root/tests/swift/SimdShim.swift" "$root/tests/swift/drift/main.swift"
"$out/drift"
