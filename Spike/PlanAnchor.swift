import Foundation
#if canImport(simd)
import simd
#endif  // Linux: tests/swift/SimdShim.swift supplies the few simd functions used here

/// The bag *interior* given its scanned outer shell: width and depth lose a wall on each side,
/// the floor rises by one wall thickness (the bag is scanned open, so the lid is not in the box),
/// which lifts the centre by half a wall.
func interiorBox(_ outer: BoxFit, wall: Float) -> BoxFit {
    BoxFit(width: outer.width - 2 * wall, depth: outer.depth - 2 * wall, height: outer.height - wall,
           center: outer.center + SIMD3<Float>(0, wall / 2, 0), axis: outer.axis)
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
