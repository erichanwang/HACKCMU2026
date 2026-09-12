#!/usr/bin/env bash
# Spike/API.swift's pure helpers (URL resolution, error-message mapping), on Linux via -DPACKAR_TEST_ONLY.
set -euo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
if ! command -v swiftc >/dev/null 2>&1; then . "$root/swift/PackPhysics/swiftenv.sh"; fi
out="$(mktemp -d)"; trap 'rm -rf "$out"' EXIT
swiftc -DPACKAR_TEST_ONLY -Onone -o "$out/api" "$root/Spike/API.swift" "$root/tests/swift/api/main.swift"
"$out/api"
