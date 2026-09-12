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

// ---------------------------------------------------------------------------
// Footprints / convex prisms (LiDAR hull support). Mirrors the "Footprints /
// convex prisms" section of physics/geometry.py.
// ---------------------------------------------------------------------------

/// A point in an object's local (x, z) plane. `SIMD2<Double>` already conforms
/// to `Codable` as a 2-element JSON array (`[x, z]`), matching the Python wire
/// format exactly, so it doubles as `SceneObject.footprint`'s element type.
public typealias FootprintPoint = SIMD2<Double>

extension SIMD2 where Scalar == Double {
    @inlinable public var z: Double { y }
    @inlinable public init(x: Double, z: Double) { self.init(x, z) }
}

/// Convex hull of 2D points (Andrew's monotone chain), CCW, no duplicates, no
/// collinear intermediate vertices. `points` need not be sorted or deduped.
public func convexHull2D(_ points: [FootprintPoint]) -> [FootprintPoint] {
    var pts = points.sorted { $0.x != $1.x ? $0.x < $1.x : $0.z < $1.z }
    var uniq: [FootprintPoint] = []
    uniq.reserveCapacity(pts.count)
    for p in pts where uniq.last != p { uniq.append(p) }
    pts = uniq
    if pts.count <= 2 { return pts }

    func cross(_ o: FootprintPoint, _ a: FootprintPoint, _ b: FootprintPoint) -> Double {
        (a.x - o.x) * (b.z - o.z) - (a.z - o.z) * (b.x - o.x)
    }
    var lower: [FootprintPoint] = []
    for p in pts {
        while lower.count >= 2 && cross(lower[lower.count - 2], lower[lower.count - 1], p) <= 0 {
            lower.removeLast()
        }
        lower.append(p)
    }
    var upper: [FootprintPoint] = []
    for p in pts.reversed() {
        while upper.count >= 2 && cross(upper[upper.count - 2], upper[upper.count - 1], p) <= 0 {
            upper.removeLast()
        }
        upper.append(p)
    }
    return Array(lower.dropLast()) + Array(upper.dropLast())
}

/// Shoelace area of a CCW polygon (positive; 0 for a segment or point).
public func polygonArea2D(_ poly: [FootprintPoint]) -> Double {
    let k = poly.count
    if k < 3 { return 0.0 }
    var s = 0.0
    for i in 0..<k {
        let a = poly[i], b = poly[(i + 1) % k]
        s += a.x * b.z - b.x * a.z
    }
    return abs(s) / 2.0
}

/// Area centroid of a CCW convex polygon. Falls back to the vertex mean for
/// degenerate (zero-area, or < 3 vertex) input.
public func polygonCentroid2D(_ poly: [FootprintPoint]) -> FootprintPoint {
    func mean(_ pts: [FootprintPoint]) -> FootprintPoint {
        var sx = 0.0, sz = 0.0
        for p in pts { sx += p.x; sz += p.z }
        let n = Double(max(pts.count, 1))
        return FootprintPoint(x: sx / n, z: sz / n)
    }
    if poly.count < 3 { return mean(poly) }
    let n = poly.count
    var aSum = 0.0, sxSum = 0.0, szSum = 0.0
    for i in 0..<n {
        let j = (i + 1) % n
        let cr = poly[i].x * poly[j].z - poly[j].x * poly[i].z
        aSum += cr
        sxSum += (poly[i].x + poly[j].x) * cr
        szSum += (poly[i].z + poly[j].z) * cr
    }
    let a = aSum / 2.0
    if abs(a) <= 1e-18 { return mean(poly) }
    return FootprintPoint(x: sxSum / (6.0 * a), z: szSum / (6.0 * a))
}

/// Sutherland-Hodgman intersection of two CCW convex polygons -> CCW polygon,
/// possibly empty. Mirrors `physics.geometry.convex_clip_2d` (the general
/// hull-vs-hull clip used for `xzOverlapArea`; distinct from the plainer
/// `_clip_convex_2d` in Collision.swift used only for the yaw-only prism
/// narrow phase's contact patch).
public func convexClip2D(_ subject: [FootprintPoint], _ clipper: [FootprintPoint]) -> [FootprintPoint] {
    var out = subject
    let m = clipper.count
    for i in 0..<m {
        if out.isEmpty { break }
        let a = clipper[i], b = clipper[(i + 1) % m]
        let ex = b.x - a.x, ez = b.z - a.z
        func inside(_ p: FootprintPoint) -> Bool { ex * (p.z - a.z) - ez * (p.x - a.x) >= -1e-15 }
        func isect(_ p: FootprintPoint, _ q: FootprintPoint) -> FootprintPoint {
            let dx = q.x - p.x, dz = q.z - p.z
            let den = ex * dz - ez * dx
            if abs(den) < 1e-18 { return q }
            let t = (ex * (a.z - p.z) - ez * (a.x - p.x)) / den
            return FootprintPoint(x: p.x + t * dx, z: p.z + t * dz)
        }
        var nxt: [FootprintPoint] = []
        var prev = out[out.count - 1]
        for cur in out {
            if inside(cur) {
                if !inside(prev) { nxt.append(isect(prev, cur)) }
                nxt.append(cur)
            } else if inside(prev) {
                nxt.append(isect(prev, cur))
            }
            prev = cur
        }
        out = nxt
    }
    return out
}

/// The entity's footprint in its LOCAL (x, z) plane as a CCW convex polygon.
/// No supplied footprint (`nil`) returns the 4 rectangle corners CCW:
/// (-hx,-hz), (hx,-hz), (hx,hz), (-hx,hz). A supplied footprint is hulled and
/// validated: >= 3 points with positive area, finite, and inside the
/// dimensions' bounding rectangle (1e-6 slack) -- the box must remain a
/// conservative envelope of the prism.
public func footprintLocal(id: String, dimensions: Vec3, footprint: [FootprintPoint]?) throws -> [FootprintPoint] {
    let hx = dimensions.x / 2.0, hz = dimensions.z / 2.0
    guard let fp = footprint else {
        return [FootprintPoint(x: -hx, z: -hz), FootprintPoint(x: hx, z: -hz),
                FootprintPoint(x: hx, z: hz), FootprintPoint(x: -hx, z: hz)]
    }
    guard fp.allSatisfy({ $0.x.isFinite && $0.z.isFinite }) else {
        throw MalformedSceneError(objectId: id, message: "\(id): invalid footprint \(fp)")
    }
    let hull = convexHull2D(fp)
    guard hull.count >= 3, polygonArea2D(hull) > 1e-12 else {
        throw MalformedSceneError(objectId: id, message: "\(id): footprint is degenerate (needs >= 3 non-collinear points)")
    }
    guard !hull.contains(where: { abs($0.x) > hx + 1e-6 || abs($0.z) > hz + 1e-6 }) else {
        throw MalformedSceneError(objectId: id, message: "\(id): footprint exceeds dimensions \(dimensions) in local x/z")
    }
    return hull
}

/// World-space vertices of the convex prism: bottom ring (local y = -hy) in
/// footprint order, then the top ring (+hy) in the same order.
public func prismVertices(_ obb: OBB, _ footprint: [FootprintPoint]) -> [Vec3] {
    let hy = obb.halfExtents.y
    var out: [Vec3] = []
    out.reserveCapacity(2 * footprint.count)
    for p in footprint { out.append(obb.center + obb.axes * Vec3(p.x, -hy, p.z)) }
    for p in footprint { out.append(obb.center + obb.axes * Vec3(p.x, hy, p.z)) }
    return out
}
