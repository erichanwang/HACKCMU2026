// Compressibility-derived compression allowance for soft/semi-rigid objects.
// Mirrors physics/compressibility.py.
//
// This is a heuristic, not a deformation simulator: an item of loose volume V
// fits any connected void of at least V/k (`SceneObject.compressibilityK`).
// We crudely reuse that same fraction, `1 - 1/k`, as the fraction of an
// object's *linear* extent along one axis that could plausibly compress away
// -- scaled by how willing the object's rigidity class is to actually use
// that headroom (`rigidityAllowanceFraction`), and hard-capped at 95% of the
// extent so nothing is ever treated as compressing itself away to nothing.

import Foundation

/// How much of an object's theoretical max compression (derived from k) it
/// is allowed to use before a physics check treats overlap/penetration as a
/// real violation, by rigidity class. `.rigid` is always 0.0 -- a rigid
/// object ignores `compressibilityK` entirely.
public let rigidityAllowanceFraction: [Rigidity: Double] = [.rigid: 0.0, .semi: 0.5, .soft: 1.0]

/// Never treat more than this fraction of an object's own extent as
/// "compressible away".
public let maxCompressionFraction = 0.95

/// How much overlap/penetration (meters), along one axis, is plausible squish
/// for `obj` rather than a real physical violation.
///
/// `extentM` is the object's own full extent along whatever axis the caller
/// is checking (a collision MTV axis, a container wall axis, ...); this
/// function is axis-agnostic -- the caller picks the extent.
///
/// Rigid objects always get 0.0, regardless of `compressibilityK`. For
/// semi/soft objects: `extentM * (1 - 1/max(1, k)) * fraction`, clamped to
/// `[0, 0.95 * extentM]`.
public func compressionAllowanceM(_ obj: SceneObject, extentM: Double) -> Double {
    if obj.rigidity == .rigid { return 0.0 }
    let fraction = rigidityAllowanceFraction[obj.rigidity] ?? 0.0
    let k = max(1.0, obj.compressibilityK)
    let allowance = extentM * (1.0 - 1.0 / k) * fraction
    return min(max(0.0, allowance), maxCompressionFraction * extentM)
}

/// Full extent (meters) of `obb` projected onto world-space unit `axis`:
/// `2 * sum(|axis . column_k| * halfExtent_k)`.
public func axisProjectedExtentM(_ obb: OBB, axis: Vec3) -> Double {
    var half = 0.0
    for k in 0..<3 {
        half += abs(dot(axis, obb.axes.column(k))) * obb.halfExtents[k]
    }
    return 2.0 * half
}

/// Total compression allowance (meters) for a colliding pair along
/// `mtvAxis`: each object's own allowance, from its own extent projected
/// onto that axis, summed.
public func combinedCollisionAllowanceM(_ a: SceneObject, _ b: SceneObject, mtvAxis: Vec3, obbA: OBB, obbB: OBB) -> Double {
    let extentA = axisProjectedExtentM(obbA, axis: mtvAxis)
    let extentB = axisProjectedExtentM(obbB, axis: mtvAxis)
    return compressionAllowanceM(a, extentM: extentA) + compressionAllowanceM(b, extentM: extentB)
}

/// Compression allowance (meters) for `obj` bulging past one container wall.
/// `wallAxisExtentM` is `obj`'s own full extent along the violated container
/// axis (the caller projects the object's OBB onto that axis with
/// `axisProjectedExtentM`, since a rotated object's extent along a given
/// container axis isn't simply one of its own `dimensions` entries). Thin
/// wrapper around `compressionAllowanceM`, kept as its own function so
/// validator call sites read clearly.
public func containerWallAllowanceM(_ obj: SceneObject, wallAxisExtentM: Double) -> Double {
    compressionAllowanceM(obj, extentM: wallAxisExtentM)
}
