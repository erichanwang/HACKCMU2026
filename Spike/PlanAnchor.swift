import Foundation
#if canImport(simd)
import simd
#endif  // Linux: tests/swift/SimdShim.swift supplies the few simd functions used here

/// The bag *interior* given its scanned outer shell: width and depth lose a wall on each side,
/// the floor rises by one wall thickness (the bag is scanned open, so the lid is not in the box),
/// which lifts the centre by half a wall. `wallHeight`/`wallDepth` default to `wall`, so existing
/// callers are unaffected; pass them separately once a real bag is measured (see
/// tests/swift/bag/main.swift) — the floor needs extra clearance for the wheel well, and the back
/// (and, symmetrically, front) wall needs extra clearance for the telescoping handle's spine.
// The handle spine only intrudes from the back (bag-frame z = +depth/2 — the end PlanAnchor's
// `origin`/`perp` already treat as the far side from bag-frame z = 0, see PlanAnchor.init below
// and tests/swift/bag/main.swift's frame comment), so `wallDepth` is paid once, from that side,
// mirroring how `wallHeight` already pays once from the floor and shifts `center.y` to match.
func interiorBox(_ outer: BoxFit, wall: Float, wallHeight: Float? = nil, wallDepth: Float? = nil) -> BoxFit {
    let wallHeight = wallHeight ?? wall
    let wallDepth = wallDepth ?? wall
    let depthIntrusion = wallDepth - wall
    let depth = outer.depth - wall - wallDepth
    let perp = SIMD3<Float>(-outer.axis.z, 0, outer.axis.x)
    let center = outer.center + SIMD3<Float>(0, wallHeight / 2, 0) - perp * (depthIntrusion / 2)
    // Pin the direction: the modeled back face (+perp) must be inset from the outer shell by
    // exactly `wallDepth`, and the front face (-perp) by exactly `wall` — never swapped. Flipping
    // `perp`'s sign here would pull volume toward the spine instead of away from it.
    assert(abs((depth / 2 - depthIntrusion / 2) - (outer.depth / 2 - wallDepth)) < 1e-4,
           "interiorBox: back face isn't inset by wallDepth — depth shift points the wrong way")
    assert(abs((-depth / 2 - depthIntrusion / 2) - (-outer.depth / 2 + wall)) < 1e-4,
           "interiorBox: front face isn't inset by wall — depth shift points the wrong way")
    return BoxFit(width: outer.width - 2 * wall, depth: depth, height: outer.height - wallHeight,
           center: center, axis: outer.axis)
}

/// Average several single-tap axis fits into one steadier direction. Each tap's `minAreaRect`
/// fit is one noisy sample (a few degrees of spread is normal); vector-mean-then-renormalize
/// approximates a circular mean well for that spread (no wraparound risk) and cuts the error by
/// roughly sqrt(N) — see tests/swift/drift for the measured before/after. Called from
/// Spike/ScanView.swift once per suitcase tap, accumulating over however many taps the user makes.
func averageAxis(_ samples: [SIMD3<Float>]) -> SIMD3<Float> {
    simd_normalize(samples.reduce(SIMD3<Float>.zero, +))
}

/// Maps a plan's bag-frame coordinates onto AR world space. Bag X = the suitcase's `axis`,
/// bag Y = world up, bag Z = `perp`; the bag origin is the interior's min corner on the table
/// (packing-core option (a), right-handed). Pure on purpose — no ARKit, no quaternions, so it
/// compiles and is tested under `swiftc` on Linux. The renderer builds the item orientation from
/// `axis` with `simd_quatf(from: SIMD3<Float>(1, 0, 0), to: anchor.axis)`.
struct PlanAnchor {
    let axis: SIMD3<Float>    // bag X, world-space unit vector
    let perp: SIMD3<Float>    // bag Z, world-space unit vector
    let origin: SIMD3<Float>  // bag (0, 0, 0) in world space

    init(interior: BoxFit, planeY: Float) {
        axis = interior.axis
        perp = SIMD3<Float>(-interior.axis.z, 0, interior.axis.x)
        origin = SIMD3<Float>(interior.center.x, planeY, interior.center.z)
            - axis * (interior.width / 2) - perp * (interior.depth / 2)
    }

    /// World centre of a placement. `position` is its MIN corner, so the `size / 2` offset every
    /// renderer owes lives here and nowhere else.
    func worldCenter(position: SIMD3<Float>, size: SIMD3<Float>) -> SIMD3<Float> {
        origin
            + axis * (position.x + size.x / 2)
            + SIMD3<Float>(0, position.y + size.y / 2, 0)
            + perp * (position.z + size.z / 2)
    }
}
