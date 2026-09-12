#!/usr/bin/env bash
# Builds and runs the mesh-walk unit test: pure re-implementation of the ARGeometrySource/
# ARGeometryElement vertex+index arithmetic, tested without ARKit.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../../.."

if ! command -v swift >/dev/null 2>&1; then . swift/PackPhysics/swiftenv.sh; fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
swiftc -Onone -o "$tmp/mesh_test" Spike/MeshWalk.swift tests/swift/mesh/main.swift
"$tmp/mesh_test"
