#!/usr/bin/env bash
# Independent adversarial check of Spike/PlanAnchor.swift, against a from-scratch matrix
# reference (see main.swift) rather than the plan/ test's own vector-sum reasoning.
set -euo pipefail
cd "$(dirname "$0")/../../.."
if ! command -v swiftc >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

out="${TMPDIR:-/tmp}/packar-anchor-adversary"
# -Onone: top-level code under -O trips a spurious exclusivity check (see tests/swift/plan/run.sh).
swiftc -Onone -o "$out" Spike/Geometry.swift Spike/PlanAnchor.swift tests/swift/SimdShim.swift tests/swift/anchor_adversary/main.swift
"$out"
