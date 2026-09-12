import Foundation
#if canImport(simd)
import simd
#endif

// Independent adversarial check of Spike/PlanAnchor.swift. Written by a different session
// than the one that wrote PlanAnchor.swift and its own test (tests/swift/plan/main.swift).
// Every check here is derived from scratch: a hand-built 4x4-style homogeneous transform
// (rotation-about-Y matrix + translation) instead of PlanAnchor's axis/perp vector sum, so
// a shared mistake in the vector formula would NOT also be baked into the reference here.

func approx(_ a: Float, _ b: Float, _ tol: Float = 1e-3) -> Bool { abs(a - b) < tol }
func approx(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ tol: Float = 1e-3) -> Bool {
    approx(a.x, b.x, tol) && approx(a.y, b.y, tol) && approx(a.z, b.z, tol)
}

// --- from-scratch reference: an explicit homogeneous transform --------------------------------
// A 4x4 matrix (columns of SIMD4<Float>) built independently of `simd`'s float4x4 (not available
// on Linux) and independently of PlanAnchor's `axis`/`perp` vector-sum code. Only ever used as
// rotate-then-translate, so built directly as that combined matrix rather than through a generic
// (and easy-to-get-backwards) 4x4 multiply.
struct Mat4 {
    var c0, c1, c2, c3: SIMD4<Float>   // columns; c3 is the translation
    func apply(_ p: SIMD3<Float>) -> SIMD3<Float> {
        let v = c0 * p.x + c1 * p.y + c2 * p.z + c3
        return SIMD3(v.x, v.y, v.z)
    }
}

/// Bag -> world transform, built from scratch: a rotation about world Y that carries local
/// +X to `axis` (derived via atan2, not via PlanAnchor's `(-axis.z, 0, axis.x)` formula), then
/// translated to `origin`. `axis` is assumed unit-length and horizontal (axis.y == 0), matching
/// what `fitBox`/`interiorBox` always hand PlanAnchor.
func referenceTransform(origin: SIMD3<Float>, axis: SIMD3<Float>) -> Mat4 {
    let theta = atan2(axis.z, axis.x)   // axis == (cos theta, 0, sin theta)
    let phi = -theta                    // right-hand rotation about +Y sending local +X to axis
    // R(0,1,0,phi) * (1,0,0) = (cos phi, 0, -sin phi); R(0,1,0,phi) * (0,0,1) = (sin phi, 0, cos phi).
    return Mat4(c0: SIMD4(cos(phi), 0, -sin(phi), 0), c1: SIMD4(0, 1, 0, 0),
                c2: SIMD4(sin(phi), 0, cos(phi), 0), c3: SIMD4(origin.x, origin.y, origin.z, 1))
}

var failures = 0
func check(_ ok: Bool, _ label: String) {
    if ok { print("  ok   \(label)") } else { print("  FAIL \(label)"); failures += 1 }
}

// =================================================================================================
// Axis 1: handedness / sign of `perp`, and the axis.x >= 0 canonicalisation in fitBox.
// =================================================================================================
print("--- axis 1: handedness ---")
for theta: Float in [0, 0.3, 0.63, .pi / 4, .pi / 2, 2.0, 3.0, -0.9, -.pi / 2] {
    let axis = SIMD3<Float>(cos(theta), 0, sin(theta))
    let bag = BoxFit(width: 1, depth: 1, height: 1, center: SIMD3(0, 0, 0), axis: axis)
    let anchor = PlanAnchor(interior: bag, planeY: 0)
    let ref = referenceTransform(origin: SIMD3(0, 0, 0), axis: axis)
    let refX = ref.apply(SIMD3(1, 0, 0)) - ref.apply(SIMD3(0, 0, 0))  // bag X in world
    let refZ = ref.apply(SIMD3(0, 0, 1)) - ref.apply(SIMD3(0, 0, 0))  // bag Z in world
    check(approx(refX, axis), "theta=\(theta): reference bag-X matches axis")
    check(approx(refZ, anchor.perp), "theta=\(theta): reference bag-Z (rotation matrix) matches anchor.perp")
}
// The exact antiparallel case (axis = -X) fitBox's `axis.x >= 0` clamp is meant to prevent,
// but PlanAnchor itself places no such restriction on `axis`. Position math stays well defined
// there (atan2 doesn't degenerate the way a from-to quaternion's rotation axis would):
let backAxis = SIMD3<Float>(-1, 0, 0)
let backBag = BoxFit(width: 1, depth: 1, height: 1, center: .zero, axis: backAxis)
let backAnchor = PlanAnchor(interior: backBag, planeY: 0)
let backRef = referenceTransform(origin: .zero, axis: backAxis)
check(approx(backRef.apply(SIMD3(0, 0, 1)), backAnchor.perp, 1e-2), "axis = -X: perp still matches the matrix reference")
print("VERDICT axis 1: clean — perp is exactly the rotation-about-Y that sends bag X to `axis`, in both")
print("  formulations, for 9 headings including the ±90 degree and antiparallel-adjacent cases.")
print("  fitBox's `axis.x >= 0` never hands PlanAnchor the true antiparallel axis (-1,0,0), and the")
print("  renderer's `simd_quatf(from:(1,0,0), to: axis)` matches this same rotation for every axis.y==0")
print("  axis except exactly axis=(-1,0,0) itself, which fitBox's clamp rules out by construction —")
print("  cannot be exercised from Linux (simd_quatf is unavailable here), so left as a documented,")
print("  practically-unreachable edge rather than a proven bug.")

// =================================================================================================
// Axis 2: frame agreement — width/depth/height axis assignment PlanAnchor assumes.
// =================================================================================================
print("--- axis 2: frame agreement ---")
// Pin the width->axis, height->up, depth->perp assignment PlanAnchor's contract promises, using
// ScannedItem's own [width, height, depth] order (see Spike/Geometry.swift ScannedItem.dimensions
// and server/app_plan.py's `width, height, depth = suitcase["dimensions"]`).
let dims: [Float] = [0.42, 0.21, 0.62]   // width, height, depth
let frameBag = BoxFit(width: dims[0], depth: dims[2], height: dims[1], center: SIMD3(0, dims[1] / 2, 0), axis: SIMD3(1, 0, 0))
let frameAnchor = PlanAnchor(interior: frameBag, planeY: 0)
// A placement filling the whole interior: its far corner must land at (width, height, depth) away
// from the min-corner origin along (axis, up, perp) respectively — not any permutation of them.
let farCorner = frameAnchor.worldCenter(position: SIMD3(dims[0], dims[1], dims[2]), size: .zero)
check(approx(farCorner - frameAnchor.origin, SIMD3(dims[0], dims[1], dims[2])),
      "far corner is dims[0] along axis, dims[1] along up, dims[2] along perp (no width/depth swap)")
print("VERDICT axis 2: clean for PlanAnchor's own contract (checked above). Cross-checked by reading")
print("  server/planner.py (container dims [width, depth, height] for packer3d's x=length,y=width,z=up),")
print("  server/app_plan.py (app position = (packer_x, packer_z, packer_y), i.e. width/height/depth),")
print("  and packing-core's PackingPlan.swift (position/size documented in that same bag frame) — all")
print("  three hops agree on width=X, height=Y, depth=Z. Note: FIXES.md's 'double-rotate' bug in")
print("  physics/packer3d_adapter.py (scene_from_packer3d vs placements_from_packer3d) is real and still")
print("  open, but it lives in the physics *grading* path only — app_plan.to_app_plan reads the raw")
print("  packer3d placement dict directly and never goes through physics.schema, so it does not reach")
print("  PlanAnchor.")

// =================================================================================================
// Axis 3: units (metres, no hidden scale factor).
// =================================================================================================
print("--- axis 3: units ---")
let unitBag = BoxFit(width: 1, depth: 1, height: 1, center: SIMD3(0, 0.5, 0), axis: SIMD3(1, 0, 0))
let unitAnchor = PlanAnchor(interior: unitBag, planeY: 0)
let big = unitAnchor.worldCenter(position: SIMD3(100, 100, 100), size: .zero) - unitAnchor.origin
check(approx(big, SIMD3(100, 100, 100)), "worldCenter scales 1:1 with its inputs (no hidden cm<->m factor)")
print("VERDICT axis 3: clean — PlanAnchor performs no scaling at all, so it cannot introduce a unit bug;")
print("  the only place units are ever converted is packer3d/packer3d/models.py's")
print("  `from_scanned_heightmap` (units=\"cm\" default, but branches to \"m\" when the payload has a")
print("  `dimensions` key — the server's own ScannedItem shape — so the metres-producing path never")
print("  hits the cm scale). examples/scanned_item.json is the OLD width/depth/height-keyed form and is")
print("  intentionally read as cm by that same branch; it is stale but not wired to PlanAnchor.")

// =================================================================================================
// Axis 4: the interior inset / floor double-count.
// =================================================================================================
print("--- axis 4: interior inset / floor ---")
let tablePlaneY: Float = 0.7
let wall: Float = 0.012
let outerShell = BoxFit(width: 0.5, depth: 0.7, height: 0.25,
                        center: SIMD3(0, tablePlaneY + 0.25 / 2, 0), axis: SIMD3(1, 0, 0))
let interior = interiorBox(outerShell, wall: wall)
let interiorFloorY = interior.center.y - interior.height / 2
// ScanView.swift's own formula for the anchor's planeY: `planeY + suitcaseWallMeters`.
let scanViewPlaneY = tablePlaneY + wall
check(approx(interiorFloorY, scanViewPlaneY), "interior's real floor equals ScanView's planeY+wall (no double count)")
let insetAnchor = PlanAnchor(interior: interior, planeY: scanViewPlaneY)
check(approx(insetAnchor.origin.y, interiorFloorY), "PlanAnchor.origin.y sits exactly on the interior floor")
print("VERDICT axis 4: clean — `interior.center.y` (raised by wall/2) is never read for `origin.y`;")
print("  PlanAnchor takes planeY as an explicit parameter, and ScanView's `planeY + suitcaseWallMeters`")
print("  independently lands on the same value as `interiorBox`'s own raised floor. Redundant, not")
print("  double-counted — pinned above.")

// =================================================================================================
// Axis 5: degenerate / extreme inputs.
// =================================================================================================
print("--- axis 5: degenerate inputs ---")

// (a) wall thicker than half the bag: interiorBox is not validated and can produce a negative
// width/depth. This is Geometry.swift's `interiorBox`, not PlanAnchor -- flagged, not fixed here.
let tinyOuter = BoxFit(width: 0.1, depth: 0.1, height: 0.1, center: SIMD3(0, 0.05, 0), axis: SIMD3(1, 0, 0))
let overThickWall = interiorBox(tinyOuter, wall: 0.2)
check(overThickWall.width < 0, "interiorBox with wall > width/2 produces a negative interior width (unvalidated upstream input)")

// (b) a bag smaller than a placement: PlanAnchor does not clamp -- the placement's far corner
// lands outside the bag's own half-extents. By contract this is the caller's (solver's) job to
// avoid, not PlanAnchor's; confirmed here so nobody assumes PlanAnchor clamps.
let smallBag = BoxFit(width: 0.2, depth: 0.2, height: 0.2, center: SIMD3(0, 0.1, 0), axis: SIMD3(1, 0, 0))
let smallAnchor = PlanAnchor(interior: smallBag, planeY: 0)
let oversizeCorner = smallAnchor.worldCenter(position: .zero, size: SIMD3(0.5, 0.5, 0.5))
let distFromOrigin = simd_dot(oversizeCorner - smallAnchor.origin, smallAnchor.axis)
check(distFromOrigin > smallBag.width, "oversized placement is not clamped to the bag interior (0.25 > 0.2 width)")

// (c) a non-unit-length axis: PlanAnchor does not normalise. A 2x-scaled axis doubles the world
// offset along that axis for the same bag-frame position -- documents that PlanAnchor trusts its
// caller (fitBox always hands it a unit vector) rather than defending against this itself.
let scaledAxis = SIMD3<Float>(2, 0, 0)
let scaledBag = BoxFit(width: 1, depth: 1, height: 1, center: .zero, axis: scaledAxis)
let scaledAnchor = PlanAnchor(interior: scaledBag, planeY: 0)
let unitBag2 = BoxFit(width: 1, depth: 1, height: 1, center: .zero, axis: SIMD3(1, 0, 0))
let unitAnchor2 = PlanAnchor(interior: unitBag2, planeY: 0)
let scaledOffset = scaledAnchor.worldCenter(position: SIMD3(1, 0, 0), size: .zero) - scaledAnchor.origin
let unitOffset = unitAnchor2.worldCenter(position: SIMD3(1, 0, 0), size: .zero) - unitAnchor2.origin
check(approx(scaledOffset, unitOffset * 2), "non-unit axis (|axis|=2) doubles the world offset (PlanAnchor trusts a unit axis, does not enforce it)")

// (d) placement at exactly the far corner, cross-checked against the from-scratch matrix reference.
let cornerBag = BoxFit(width: 0.4, depth: 0.6, height: 0.2, center: SIMD3(1, 0.8, 2), axis: SIMD3(cos(Float(0.63)), 0, sin(Float(0.63))))
let cornerAnchor = PlanAnchor(interior: cornerBag, planeY: 0.7)
let cornerRef = referenceTransform(origin: cornerAnchor.origin, axis: cornerBag.axis)
let farPos = SIMD3<Float>(cornerBag.width, cornerBag.height, cornerBag.depth)
let farSize = SIMD3<Float>(0, 0, 0)
let farAnchorResult = cornerAnchor.worldCenter(position: farPos, size: farSize)
let farRefResult = cornerRef.apply(farPos)
check(approx(farAnchorResult, farRefResult), "far-corner placement matches the from-scratch matrix reference")

// (e) bag axis along -x and -z, matrix-checked (not just the tautological cross-product identity).
for axis in [SIMD3<Float>(-1, 0, 0), SIMD3<Float>(0, 0, -1), SIMD3<Float>(0, 0, 1)] {
    let bag = BoxFit(width: 0.3, depth: 0.5, height: 0.2, center: SIMD3(2, 0.9, -1), axis: axis)
    let anchor = PlanAnchor(interior: bag, planeY: 0.8)
    let ref = referenceTransform(origin: anchor.origin, axis: axis)
    let pos = SIMD3<Float>(0.1, 0.05, 0.2), size = SIMD3<Float>(0.05, 0.05, 0.05)
    check(approx(anchor.worldCenter(position: pos, size: size), ref.apply(pos + size / 2)),
          "axis=\(axis): matrix reference matches PlanAnchor")
}

// (f) a 45-degree bag, matrix-checked end to end (position + size offset together).
let ang45: Float = .pi / 4
let bag45 = BoxFit(width: 0.4, depth: 0.4, height: 0.3, center: SIMD3(0.5, 0.85, 0.5), axis: SIMD3(cos(ang45), 0, sin(ang45)))
let anchor45 = PlanAnchor(interior: bag45, planeY: 0.7)
let ref45 = referenceTransform(origin: anchor45.origin, axis: bag45.axis)
let pos45 = SIMD3<Float>(0.1, 0.05, 0.15), size45 = SIMD3<Float>(0.1, 0.1, 0.1)
check(approx(anchor45.worldCenter(position: pos45, size: size45), ref45.apply(pos45 + size45 / 2)),
      "45-degree bag matches the matrix reference")

print("VERDICT axis 5: clean for PlanAnchor's own math on every degenerate case that reaches it (oversized")
print("  placement, non-unit axis, far corner, -x/-z/45-degree headings) -- it does exactly the documented")
print("  arithmetic with no hidden clamping in any of them, matched against the from-scratch matrix")
print("  reference to the tolerance used throughout this file. (a) is a real gap, but it is in")
print("  Spike/Geometry.swift's `interiorBox`, not in PlanAnchor.swift, so it is reported, not fixed here.")

if failures > 0 {
    print("\n\(failures) FAILURE(S)")
    exit(1)
}
print("\nanchor adversary: all checks passed against an independent matrix reference — ok")
