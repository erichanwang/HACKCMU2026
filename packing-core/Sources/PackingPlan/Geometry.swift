import Foundation

/// A bag-frame axis.
///
/// Per the project conventions: `x` is width, `y` is up, `z` is depth.
public enum Axis: Int, CaseIterable, Codable, Sendable {
    case x = 0
    case y = 1
    case z = 2

    public var label: String {
        switch self {
        case .x: return "X (width)"
        case .y: return "Y (up)"
        case .z: return "Z (depth)"
        }
    }
}

/// A point or extent in the bag-local frame. **Always meters.**
public struct Vector3: Hashable, Codable, Sendable {
    public var x: Float
    public var y: Float
    public var z: Float

    public init(x: Float, y: Float, z: Float) {
        self.x = x
        self.y = y
        self.z = z
    }

    public init(_ x: Float, _ y: Float, _ z: Float) {
        self.init(x: x, y: y, z: z)
    }

    public static let zero = Vector3(0, 0, 0)

    public subscript(axis: Axis) -> Float {
        get {
            switch axis {
            case .x: return x
            case .y: return y
            case .z: return z
            }
        }
        set {
            switch axis {
            case .x: x = newValue
            case .y: y = newValue
            case .z: z = newValue
            }
        }
    }

    /// Bridge for RealityKit / SIMD math. `SIMD3` is stdlib, so this stays portable.
    public var simd: SIMD3<Float> { SIMD3(x, y, z) }

    public var volume: Float { x * y * z }

    public static func + (lhs: Vector3, rhs: Vector3) -> Vector3 {
        Vector3(lhs.x + rhs.x, lhs.y + rhs.y, lhs.z + rhs.z)
    }

    public static func - (lhs: Vector3, rhs: Vector3) -> Vector3 {
        Vector3(lhs.x - rhs.x, lhs.y - rhs.y, lhs.z - rhs.z)
    }

    public static func * (lhs: Vector3, rhs: Float) -> Vector3 {
        Vector3(lhs.x * rhs, lhs.y * rhs, lhs.z * rhs)
    }

    public static func / (lhs: Vector3, rhs: Float) -> Vector3 {
        Vector3(lhs.x / rhs, lhs.y / rhs, lhs.z / rhs)
    }
}

extension Vector3: CustomStringConvertible {
    /// Millimetre precision is enough to read at a glance and enough to spot a
    /// half-size offset bug.
    public var description: String {
        String(format: "(%.3f, %.3f, %.3f)", x, y, z)
    }
}

/// An axis-aligned box in the bag-local frame, stored as **min corner + size** —
/// the same representation the plan JSON uses.
///
/// This type is the single place that knows how to turn a plan position into a
/// render center. Never recompute `center` by hand at a call site.
public struct BoundingBox: Hashable, Sendable {
    /// The corner nearest the origin on all three axes. This is a placement's
    /// `position` verbatim — *not* the centroid.
    public let minCorner: Vector3

    /// Full extent on each axis (not a half-extent).
    public let size: Vector3

    public init(minCorner: Vector3, size: Vector3) {
        self.minCorner = minCorner
        self.size = size
    }

    /// The corner farthest from the origin: `minCorner + size`.
    public var maxCorner: Vector3 { minCorner + size }

    /// The centroid — **this is what RealityKit wants** when positioning a box
    /// entity, and the `size / 2` term here is the offset that is so easy to
    /// forget. See CLAUDE.md.
    public var center: Vector3 { minCorner + size / 2 }

    public var volume: Float { size.volume }

    /// Signed overlap extent per axis against `other`.
    ///
    /// A component `<= 0` means the boxes are separated (or exactly touching) on
    /// that axis, which is enough to prove they do not intersect.
    public func overlapExtents(with other: BoundingBox) -> Vector3 {
        var result = Vector3.zero
        for axis in Axis.allCases {
            let low = max(minCorner[axis], other.minCorner[axis])
            let high = min(maxCorner[axis], other.maxCorner[axis])
            result[axis] = high - low
        }
        return result
    }

    /// True when the two boxes share interior volume.
    ///
    /// Face contact is **not** an intersection: stacking an item directly on top
    /// of another is a normal, valid plan. `tolerance` (default 1 µm) absorbs
    /// float noise around exactly-touching faces while still catching any real,
    /// millimetre-scale overlap.
    public func intersects(_ other: BoundingBox, tolerance: Float = 1e-6) -> Bool {
        let overlap = overlapExtents(with: other)
        return Axis.allCases.allSatisfy { overlap[$0] > tolerance }
    }

    /// Per-axis amount by which this box pokes out of `other`, clamped at 0.
    ///
    /// All components `0` means fully contained.
    public func overshoot(outOf other: BoundingBox, tolerance: Float = 1e-6) -> Vector3 {
        var result = Vector3.zero
        for axis in Axis.allCases {
            let under = other.minCorner[axis] - minCorner[axis]
            let over = maxCorner[axis] - other.maxCorner[axis]
            let worst = max(under, over)
            result[axis] = worst > tolerance ? worst : 0
        }
        return result
    }

    public func isContained(in other: BoundingBox, tolerance: Float = 1e-6) -> Bool {
        overshoot(outOf: other, tolerance: tolerance) == .zero
    }
}

extension BoundingBox: CustomStringConvertible {
    public var description: String {
        "min \(minCorner) → max \(maxCorner) (size \(size))"
    }
}
