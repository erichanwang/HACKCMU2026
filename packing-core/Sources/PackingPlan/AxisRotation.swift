import Foundation

/// An axis-aligned orientation: which of the item's own axes lands on each bag axis.
///
/// Read the raw string left to right as the item-local axes assigned to bag
/// **X, Y, Z** in that order. So `.xyz` is the identity, and `.zxy` means
/// item-local Z runs along bag X, item-local X runs along bag Y, and item-local
/// Y runs along bag Z.
///
/// Important: a placement's `size` is already the extent **in bag axes**, i.e.
/// after this permutation is applied. Renderers must not permute `size` again —
/// the rotation exists to orient the item's mesh and its label, not to re-derive
/// its footprint. `position + size` remains the occupied box in every case, so
/// the `size / 2` centering rule is unaffected by rotation.
public enum AxisRotation: String, Codable, CaseIterable, Sendable {
    case xyz = "XYZ"
    case xzy = "XZY"
    case yxz = "YXZ"
    case yzx = "YZX"
    case zxy = "ZXY"
    case zyx = "ZYX"

    /// The item-local axis that runs along the given bag axis.
    public func localAxis(forBagAxis bagAxis: Axis) -> Axis {
        let characters = Array(rawValue)
        switch characters[bagAxis.rawValue] {
        case "X": return .x
        case "Y": return .y
        default: return .z
        }
    }

    /// The bag axis that the given item-local axis runs along — the inverse map.
    public func bagAxis(forLocalAxis localAxis: Axis) -> Axis {
        for candidate in Axis.allCases where self.localAxis(forBagAxis: candidate) == localAxis {
            return candidate
        }
        // Unreachable: every case's raw value is a permutation of X, Y, Z.
        return localAxis
    }

    /// Re-express an item's intrinsic size in bag axes under this rotation.
    ///
    /// Use it to check a solver's `size` field, or to label an item with its own
    /// dimensions; do not use it to transform a `size` that is already bag-space.
    public func bagExtent(ofLocalSize localSize: Vector3) -> Vector3 {
        var result = Vector3.zero
        for bag in Axis.allCases {
            result[bag] = localSize[localAxis(forBagAxis: bag)]
        }
        return result
    }

    /// Whether the permutation is even. Odd permutations are reflections, not
    /// rotations, on their own — see `basisColumns`.
    public var isEvenPermutation: Bool {
        switch self {
        case .xyz, .yzx, .zxy: return true
        case .xzy, .yxz, .zyx: return false
        }
    }

    /// Columns of the 3×3 basis mapping an item-local vector into bag axes.
    ///
    /// The three odd permutations are reflections (determinant −1), which no
    /// rigid rotation can produce. Since an axis-aligned box is symmetric under
    /// a per-axis flip, the third column is negated in those cases to give a
    /// proper rotation (determinant +1) that renders identically. Build a
    /// RealityKit basis with
    /// `simd_float3x3(columns.0, columns.1, columns.2)` and derive a quaternion
    /// from it.
    public var basisColumns: (SIMD3<Float>, SIMD3<Float>, SIMD3<Float>) {
        func unit(_ axis: Axis, negated: Bool) -> SIMD3<Float> {
            var v = SIMD3<Float>(repeating: 0)
            v[axis.rawValue] = negated ? -1 : 1
            return v
        }
        let flipLast = !isEvenPermutation
        return (
            unit(bagAxis(forLocalAxis: .x), negated: false),
            unit(bagAxis(forLocalAxis: .y), negated: false),
            unit(bagAxis(forLocalAxis: .z), negated: flipLast)
        )
    }
}
