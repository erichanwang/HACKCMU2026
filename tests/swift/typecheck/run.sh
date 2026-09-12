#!/usr/bin/env bash
# Linux type-check harness for Spike/*.swift.
#
# WHAT THIS PROVES: that the app's own code is internally type-consistent against the
# signatures declared in Shims/*.swift (hand-written stand-ins for ARKit, RealityKit, UIKit,
# SwiftUI, Metal and simd — none of which exist on Linux). Every real compiler error reported
# below (mismatched argument types, wrong optionality, actor-isolation violations against the
# isolation these shims declare, etc.) is a genuine bug against those declared signatures.
#
# WHAT THIS DOES NOT PROVE:
#  - That the shim signatures match Apple's real SDK. Every shim cites its source (a docs URL)
#    in its file; anywhere this pass was unsure, it is listed as "unverified" in the harness's
#    report rather than silently guessed.
#  - That anything actually runs. Every shim body is `fatalError()` — this is types only.
#  - SwiftUI view-builder semantics. Spike/ScanView.swift is fully typechecked (it has no `body`
#    of its own — see its file — so this sidesteps @ViewBuilder/opaque-return-type entirely).
#    Spike/SpikeApp.swift is NOT: its `ContentView.body`/`ItemEditor.body` are real SwiftUI view
#    trees (ZStack/VStack/Picker/Form/... chains), and a hand-rolled @ViewBuilder is exactly
#    where a shim is most likely to silently diverge from the real compiler's overload
#    resolution — a "pass" there would be false confidence, not a check. SpikeApp.swift only
#    gets `swiftc -parse` (syntax only), same as before this harness existed.
#  - Objective-C interop. Linux Swift has no Objective-C runtime at all — confirmed empirically:
#    `@objc`/`#selector` fail with "Objective-C interoperability is disabled" no matter what a
#    shim declares. Spike/ScanView.swift's `@objc func tap` and its `#selector(Coordinator.tap)`
#    call (lines noted below) are a hard platform limitation, not a code bug — this script
#    allowlists exactly those two diagnostics by exact message text and fails on anything else.
#  - That these are the flags Xcode will actually use. project.yml pins no SWIFT_VERSION or
#    concurrency setting. This harness checks under `-swift-version 6` (Swift 6 language mode,
#    complete concurrency checking) as the stricter, superset choice — checked empirically to
#    produce a superset of what `-swift-version 5` (this toolchain's un-pinned default) finds
#    against these shims. If Xcode actually builds this target in Swift 5 mode, one of the
#    findings below (the `UIColor`-array Sendable one) will not reproduce there.
set -uo pipefail
root="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$root"
if ! command -v swiftc >/dev/null 2>&1; then
    export PATH="$HOME/.local/share/swiftly/bin:$PATH"
    export LD_LIBRARY_PATH="$HOME/.local/swift-compat/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

here="tests/swift/typecheck"
mods="$here/.modcache"
mkdir -p "$mods"
fail=0

# Fake SDK modules, one per Apple framework Spike/*.swift imports, built from Shims/*.swift.
# Order matters: each depends only on modules built before it (see each file's own imports).
build_mod() {
    swiftc -emit-module -parse-as-library -module-name "$1" -I "$mods" \
        -emit-module-path "$mods/$1.swiftmodule" "$here/Shims/$2" || fail=1
}
build_mod simd SimdShim.swift
build_mod Metal MetalShim.swift
build_mod UIKit UIKitShim.swift
build_mod ARKit ARKitShim.swift
build_mod RealityKit RealityKitShim.swift
build_mod SwiftUI SwiftUIShim.swift

# The real PackingPlan package (pure Swift, no Apple frameworks) — API.swift and ScanView.swift
# use it for real, not a shim.
swift build --package-path packing-core --product PackingPlan >/dev/null 2>&1 || fail=1
plan_mods="packing-core/.build/x86_64-unknown-linux-gnu/debug/Modules"

if [ $fail -ne 0 ]; then
    echo "FAIL: could not build the shim/PackingPlan modules"
    exit 1
fi

out="$(swiftc -typecheck -swift-version 6 -I "$mods" -I "$plan_mods" \
    Spike/Geometry.swift Spike/MeshWalk.swift Spike/PlanAnchor.swift Spike/ScanValidation.swift \
    Spike/ScanView.swift Spike/API.swift 2>&1)"

echo "$out"

# Every real "error:" line, minus the two lines that are a Linux platform limitation
# (no Objective-C runtime at all — see header) rather than a bug in the app's code.
unexpected="$(echo "$out" | grep "error:" \
    | grep -v "Objective-C interoperability is disabled" \
    | grep -v "'#selector' can only be used with the Objective-C runtime")"

if [ -n "$unexpected" ]; then
    echo "UNEXPECTED type errors (not the known @objc/#selector platform limitation):"
    echo "$unexpected"
    fail=1
fi

# Spike/SpikeApp.swift: syntax only — see header for why (SwiftUI view-builder fidelity is out
# of scope for this harness).
swiftc -parse Spike/SpikeApp.swift || fail=1

if [ $fail -eq 0 ]; then
    echo "typecheck: GREEN (2 known @objc/#selector diagnostics allowlisted, see header)"
else
    echo "typecheck: RED"
fi
exit $fail
