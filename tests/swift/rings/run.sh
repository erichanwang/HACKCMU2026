#!/usr/bin/env bash
# Spike/InventoryRings.swift's pure ring layout (slot assignment, spin), on Linux via -DPACKAR_TEST_ONLY.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
if ! command -v swiftc >/dev/null 2>&1; then . "$root/swift/PackPhysics/swiftenv.sh"; fi
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
swiftc -DPACKAR_TEST_ONLY -Onone -o "$out/rings" "$root/Spike/InventoryRings.swift" "$root/tests/swift/rings/main.swift"
"$out/rings"
