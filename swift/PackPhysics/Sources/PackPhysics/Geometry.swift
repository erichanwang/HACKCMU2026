// Shared OBB geometry: quaternion -> world axes, half-extents, vertices.
// Mirrors physics/geometry.py. Every module builds OBBs through `obbFrom` so the
// whole layer agrees on one quaternion convention. Pure stdlib (no `simd` module)
// so this compiles on Linux for CI and on iOS unchanged.

import Foundation

@inlinable public func dot(_ a: Vec3, _ b: Vec3) -> Double { (a * b).sum() }

@inlinable public func cross(_ a: Vec3, _ b: Vec3) -> Vec3 {
    Vec3(a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x)
}

@inlinable public func length(_ a: Vec3) -> Double { dot(a, a).squareRoot() }

/// 3x3 matrix stored as three COLUMN vectors. For an OBB, column k is the
/// object's local axis k expressed in world coordinates (same as numpy's
/// `axes[:, k]` in the Python layer).
public struct Mat3: Equatable, Sendable {
    public var c0: Vec3
    public var c1: Vec3
    public var c2: Vec3

    public init(columns c0: Vec3, _ c1: Vec3, _ c2: Vec3) {
        self.c0 = c0; self.c1 = c1; self.c2 = c2
    }

    public static let identity = Mat3(columns: Vec3(1, 0, 0), Vec3(0, 1, 0), Vec3(0, 0, 1))

    @inlinable public func column(_ k: Int) -> Vec3 {
        switch k {
        case 0: return c0
        case 1: return c1
        default: return c2
        }
    }

    /// m[row, col]
    @inlinable public subscript(row: Int, col: Int) -> Double {
        column(col)[row]
    }

    /// Matrix * column vector (world = axes * local).
    @inlinable public static func * (m: Mat3, v: Vec3) -> Vec3 {
        m.c0 * v.x + m.c1 * v.y + m.c2 * v.z
    }

    /// Transpose * vector, i.e. project `v` onto the columns (world -> local).
    @inlinable public func transposeMultiply(_ v: Vec3) -> Vec3 {
        Vec3(dot(c0, v), dot(c1, v), dot(c2, v))
    }

    @inlinable public static func * (a: Mat3, b: Mat3) -> Mat3 {
        Mat3(columns: a * b.c0, a * b.c1, a * b.c2)
    }

    public var transposed: Mat3 {
        Mat3(columns: Vec3(c0.x, c1.x, c2.x), Vec3(c0.y, c1.y, c2.y), Vec3(c0.z, c1.z, c2.z))
    }
}

/// A `ValueError` from the Python layer: names the offending object (or container).
public struct MalformedSceneError: Error, Equatable, CustomStringConvertible {
    public let objectId: String?
    public let message: String

    public init(objectId: String?, message: String) {
        self.objectId = objectId
        self.message = message
    }

    public var description: String { message }
}

/// (x, y, z, w) -> rotation matrix. Same rule as Python `quat_to_matrix`: a zero or
/// non-finite norm is an error; a norm off by more than 1e-3 is re-normalized.
public func quatToMatrix(_ q: Quat, id: String? = nil) throws -> Mat3 {
    var (x, y, z, w) = (q.x, q.y, q.z, q.w)
    let n = x * x + y * y + z * z + w * w
    guard n.isFinite, n >= 1e-12 else {
        throw MalformedSceneError(objectId: id, message: "\(id ?? "?"): invalid quaternion (\(q.x), \(q.y), \(q.z), \(q.w)): zero or non-finite norm")
    }
    if abs(n - 1.0) > 1e-3 {
        let s = 1.0 / n.squareRoot()
        x *= s; y *= s; z *= s; w *= s
    }
    // Rows of the standard quaternion rotation matrix, stored by column.
    let r0 = Vec3(1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w))
    let r1 = Vec3(2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w))
    let r2 = Vec3(2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y))
    return Mat3(columns: Vec3(r0.x, r1.x, r2.x), Vec3(r0.y, r1.y, r2.y), Vec3(r0.z, r1.z, r2.z))
}

public struct OBB: Equatable {
    public var center: Vec3
    /// Columns are the world-space unit local x / y / z axes.
    public var axes: Mat3
    public var halfExtents: Vec3
    public var id: String

    public init(center: Vec3, axes: Mat3, halfExtents: Vec3, id: String) {
        self.center = center
        self.axes = axes
        self.halfExtents = halfExtents
        self.id = id
    }
}

/// Corner sign pattern in the SAME fixed order as Python `geometry.SIGNS`:
/// index bits are (x, y, z) signs: 0=(−,−,−), 1=(−,−,+), 2=(−,+,−), 3=(−,+,+),
/// 4=(+,−,−), 5=(+,−,+), 6=(+,+,−), 7=(+,+,+).
public let SIGNS: [Vec3] = [
    Vec3(-1, -1, -1), Vec3(-1, -1, 1), Vec3(-1, 1, -1), Vec3(-1, 1, 1),
    Vec3(1, -1, -1), Vec3(1, -1, 1), Vec3(1, 1, -1), Vec3(1, 1, 1),
]

@inlinable func isFinite(_ v: Vec3) -> Bool { v.x.isFinite && v.y.isFinite && v.z.isFinite }

private func makeOBB(id: String, dimensions: Vec3, position: Vec3, rotation: Quat) throws -> OBB {
    guard isFinite(dimensions), dimensions.x > 0, dimensions.y > 0, dimensions.z > 0 else {
        throw MalformedSceneError(objectId: id, message: "\(id): invalid dimensions \(dimensions)")
    }
    guard isFinite(position) else {
        throw MalformedSceneError(objectId: id, message: "\(id): invalid position \(position)")
    }
    let axes = try quatToMatrix(rotation, id: id)
    return OBB(center: position, axes: axes, halfExtents: dimensions / 2.0, id: id)
}

public func obbFrom(_ o: SceneObject) throws -> OBB {
    try makeOBB(id: o.id, dimensions: o.dimensions, position: o.position, rotation: o.rotation)
}

public func obbFrom(_ c: Container) throws -> OBB {
    try makeOBB(id: c.id, dimensions: c.dimensions, position: c.position, rotation: c.rotation)
}

/// The 8 world-space corners, in `SIGNS` order.
public func obbVertices(_ obb: OBB) -> [Vec3] {
    SIGNS.map { s in obb.center + obb.axes * (s * obb.halfExtents) }
}
