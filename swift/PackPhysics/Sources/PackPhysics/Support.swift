// Static stability heuristic for packed objects. Port of physics/support.py.
//
// This is a STATIC STABILITY HEURISTIC, NOT a full rigid-body simulator. It does
// not integrate forces/torques over time, does not model friction, and does not
// predict dynamic tipping under acceleration (braking, turning, being dropped).
// It answers one narrower question: "given these final resting poses, does each
// object look adequately supported and balanced under gravity alone?"
//
// Model (geometrically exact for boxes):
// - Rigid bodies, uniform density per object, so an object's center of mass is its
//   OBB center; its COM projection is that center's (X, Z).
// - Gravity acts along -Y (Y is up, per Schema.swift).
// - Contact footprints are exact convex polygons in the XZ plane. An object's
//   bottom contact set is the subset of its 8 world-space corners with
//   `y <= aabbMin.y + epsilon`; the bottom footprint is the 2D convex hull
//   (monotone chain) of those corners projected to XZ — 4 points for a face-down
//   box at any yaw, 2 for edge contact, 1 for corner contact. A supporter's top
//   contact set is likewise the corners with `y >= aabbMax.y - epsilon`. The
//   container floor is a single flat plane at `containerFloorY` whose footprint is
//   the XZ hull of the container's 4 lowest corners (so a yaw-rotated or mildly
//   tilted container still works; a heavily tilted container, where "the floor" is
//   no longer one horizontal plane, is out of scope).
// - A support patch is the convex polygon intersection (Sutherland–Hodgman) of the
//   object's bottom footprint with each supporter's top footprint, for every
//   supporter reported by `restingPairs` plus the container floor when `onFloor`.
//   Patch areas come from the shoelace formula.
// - PATCHES-ARE-DISJOINT ASSUMPTION: `covered = sum(patch areas)`, clamped to the
//   bottom footprint area. Exact for the collision-free scenes this layer is given:
//   two supporters touching the same object at the same height cannot overlap in XZ
//   without interpenetrating. In an already-colliding scene (validated separately)
//   overlapping supporters would be double-counted, which the clamp caps at 1.0.
// - DEGENERATE CONTACT: when the bottom footprint has area < 1e-9 m^2 (edge or
//   corner contact — a hull that is a segment or a single point) area ratios are
//   meaningless, so `supportRatio` is instead the fraction of the bottom contact
//   POINTS lying inside, or within 1e-9 m of, some supporter's top footprint (floor
//   included). A box balanced on one bottom edge with both contact corners on a
//   supporter reports 1.0, one corner reports 0.5.
// - STABILITY: the support polygon is the convex hull of the union of all support
//   patch vertices, and `stabilityMarginM` is the signed 2D distance from the COM
//   projection to the nearest point of that hull's boundary — positive inside,
//   negative outside (the standard static criterion). When the support polygon
//   degenerates to a segment or a point there is no inside, so the margin is minus
//   the distance to it. TIE-BREAK: a margin in (-1e-9, 0) is snapped to exactly
//   0.0, so a perfectly edge-balanced box reads margin 0.0 and `unstable == false`
//   (`unstable` is `margin < 0`). Boundary counts as supported; float noise does not
//   decide the verdict.
// - No support at all (empty support polygon) reports
//   `stabilityMarginM = floatingMarginSentinelM` rather than a real distance.
// - CHAIN INSTABILITY: `supportedByUnstable` is true when any object this one rests
//   on is itself `floating`, `unstable`, or `supportedByUnstable`. Computed bottom-up
//   in ascending `aabbMin.y` order (a supporter always has a lower bottom than the
//   object resting on it), so "A destabilizes B destabilizes C" propagates up the
//   whole stack.
//
// Fast paths (pure speed; each returns exactly what the general code would, bit for
// bit): when a contact set is the 4 corners of one box face (the common face-down
// case at any yaw) its XZ hull is those 4 corners in known cyclic order, so the
// monotone chain is skipped — but only when the projected face is strictly convex,
// otherwise `hull` runs; when an object sits wholly inside one rectangular floor the
// floor clip cannot cut its footprint; and when an object has exactly one support
// patch equal to its own bottom footprint, that footprint already IS the support
// polygon.
//
// Known failure modes / out of scope: no friction; no dynamics (no acceleration,
// vibration, impact or toppling, and no normal-force distribution, so a
// supported-but-overloaded shelf looks like a sound one — mass only reaches the
// constraints layer); uniform density; a single flat floor plane; patches assumed
// disjoint; rigid bodies, so `rigidity`/`compressibilityK` are not consulted and a
// squashed soft item's real (larger) contact patch is not modelled.

import Foundation

public let floatingMarginSentinelM = -1.0e6
/// Hull areas below this are an edge/corner contact, not a face contact.
private let degenerateAreaM2 = 1e-9
/// "On the boundary" tolerance: point-in-polygon slack and the margin tie-break.
private let touchTolM = 1e-9

public struct SupportResult: Equatable {
    public var objectId: String
    public var supportRatio: Double
    public var stabilityMarginM: Double
    /// `"container_floor"` first (when on the floor), then supporters in scene order.
    public var supportingObjects: [String]
    public var floating: Bool
    public var unstable: Bool
    /// True if anything this object rests on is floating/unstable/itself standing on
    /// something unstable (chain reaction).
    public var supportedByUnstable: Bool
    /// Support polygon (convex hull of all support patches) as [x, z] pairs, empty
    /// when unsupported. Renderer diagnostic.
    public var contactPolygon: [[Double]]
    /// supporter id ("container_floor" included) -> contact patch area m^2.
    public var patchAreasM2: [String: Double]
}

/// A point in the XZ plane. Ordering is Python tuple ordering on `(x, z)`.
private struct Pt: Equatable, Comparable {
    var x: Double
    var z: Double

    static func < (a: Pt, b: Pt) -> Bool { a.x < b.x || (a.x == b.x && a.z < b.z) }
}

private func cross2(_ o: Pt, _ a: Pt, _ b: Pt) -> Double {
    (a.x - o.x) * (b.z - o.z) - (a.z - o.z) * (b.x - o.x)
}

/// Convex hull of XZ points (monotone chain), CCW, collinear points dropped.
/// Returns 1 or 2 points for a degenerate (point / segment) set.
private func hull(_ pts: [Pt]) -> [Pt] {
    var sorted = pts.sorted()
    var uniq: [Pt] = []
    uniq.reserveCapacity(sorted.count)
    for p in sorted where uniq.last != p { uniq.append(p) }
    sorted = uniq
    if sorted.count <= 2 { return sorted }

    func half(_ seq: [Pt]) -> [Pt] {
        var out: [Pt] = []
        for p in seq {
            while out.count >= 2 && cross2(out[out.count - 2], out[out.count - 1], p) <= 0.0 {
                out.removeLast()
            }
            out.append(p)
        }
        return out
    }

    let lower = half(sorted)
    let upper = half(sorted.reversed())
    let h = Array(lower.dropLast()) + Array(upper.dropLast())
    return h.count >= 3 ? h : [sorted[0], sorted[sorted.count - 1]]
}

/// corner-index bitmask -> that set's 4 corners in cyclic (rectangle) order.
///
/// One entry per box face, derived from `SIGNS` so it cannot drift from the corner
/// order `SceneGeometry.vertices` actually uses. A face is the 4 corners sharing one
/// local axis sign; walking the other two axes' sign pairs as (-,-) (-,+) (+,+) (+,-)
/// traverses the rectangle, not its diagonal.
private let faceCycles: [Int: (Int, Int, Int, Int)] = {
    var cycles: [Int: (Int, Int, Int, Int)] = [:]
    for axis in 0..<3 {
        let others = (0..<3).filter { $0 != axis }
        let (b, c) = (others[0], others[1])
        for sign in [-1.0, 1.0] {
            let rows = (0..<8).filter { SIGNS[$0][axis] == sign }
            let cycle = [(-1.0, -1.0), (-1.0, 1.0), (1.0, 1.0), (1.0, -1.0)].map { sb, sc in
                rows.first { SIGNS[$0][b] == sb && SIGNS[$0][c] == sc }!
            }
            cycles[cycle.reduce(0) { $0 | (1 << $1) }] = (cycle[0], cycle[1], cycle[2], cycle[3])
        }
    }
    return cycles
}()

/// Corner indices of a bitmask, ascending (same order as a boolean-mask select).
private func bitIndices(_ mask: Int) -> [Int] {
    (0..<8).filter { mask >> $0 & 1 == 1 }
}

/// `hull` of the 4 corners of one box face, without the monotone chain.
///
/// `xz` is one object's 8 corners projected to (x, z) in SIGNS order, `cycle` the
/// face's corners in cyclic order (from `faceCycles`, nil for any other contact set).
/// A box face projects to a parallelogram, so when it is strictly convex its hull is
/// exactly those 4 points — emitted CCW and rotated to start at the lexicographically
/// smallest one, which is precisely what `hull` returns. Returns nil (-> caller falls
/// back to `hull`) for anything else: a non-face contact set, or a projection that is
/// a segment or a point (vertical face, coincident corners) where the chain's
/// collinear handling has to decide.
private func faceHull(_ xz: [Pt], _ cycle: (Int, Int, Int, Int)?) -> [Pt]? {
    guard let cycle = cycle else { return nil }
    let p0 = xz[cycle.0], p1 = xz[cycle.1], p2 = xz[cycle.2], p3 = xz[cycle.3]
    let (x0, z0) = (p0.x, p0.z)
    let (x1, z1) = (p1.x, p1.z)
    let (x2, z2) = (p2.x, p2.z)
    let (x3, z3) = (p3.x, p3.z)
    let t0 = (x1 - x0) * (z2 - z0) - (z1 - z0) * (x2 - x0)
    let t1 = (x2 - x1) * (z3 - z1) - (z2 - z1) * (x3 - x1)
    let t2 = (x3 - x2) * (z0 - z2) - (z3 - z2) * (x0 - x2)
    let t3 = (x0 - x3) * (z1 - z3) - (z0 - z3) * (x1 - x3)
    let quad: [Pt]
    if t0 > 0.0 && t1 > 0.0 && t2 > 0.0 && t3 > 0.0 {
        quad = [p0, p1, p2, p3]
    } else if t0 < 0.0 && t1 < 0.0 && t2 < 0.0 && t3 < 0.0 {
        quad = [p3, p2, p1, p0]
    } else {
        return nil  // collinear / degenerate projection: let `hull` decide
    }
    let start = quad.firstIndex(of: quad.min()!)!
    return Array(quad[start...]) + Array(quad[..<start])
}

/// Shoelace area (unsigned). 0.0 for a segment or point.
private func polyArea(_ poly: [Pt]) -> Double {
    let k = poly.count
    if k < 3 { return 0.0 }
    // Term order (0..k-2, then the wrap-around term) is the summation order of the
    // textbook loop — float addition is not associative, so it is kept.
    var s = 0.0
    var x1 = poly[0].x, z1 = poly[0].z
    for i in 1..<k {
        let (x2, z2) = (poly[i].x, poly[i].z)
        s += x1 * z2 - x2 * z1
        x1 = x2; z1 = z2
    }
    let (x2, z2) = (poly[0].x, poly[0].z)
    return abs(s + (x1 * z2 - x2 * z1)) / 2.0
}

private func segDist(_ p: Pt, _ a: Pt, _ b: Pt) -> Double {
    let dx = b.x - a.x, dz = b.z - a.z
    let d2 = dx * dx + dz * dz
    let t = d2 <= 0.0 ? 0.0 : max(0.0, min(1.0, ((p.x - a.x) * dx + (p.z - a.z) * dz) / d2))
    return hypot(p.x - (a.x + t * dx), p.z - (a.z + t * dz))
}

/// Signed distance from `p` to convex `poly` (CCW): + inside, - outside, magnitude =
/// distance to the boundary. Degenerate polys (segment, point) have no inside, so the
/// result is always <= 0.
private func signedDist(_ p: Pt, _ poly: [Pt]) -> Double {
    if poly.isEmpty { return floatingMarginSentinelM }
    if poly.count == 1 { return -hypot(p.x - poly[0].x, p.z - poly[0].z) }
    if poly.count == 2 { return -segDist(p, poly[0], poly[1]) }
    // `cross2(a, b, p)` and `segDist(p, a, b)` inlined and sharing their
    // subexpressions: same operands, same operation order, same bits.
    let (px, pz) = (p.x, p.z)
    var inside = true
    var dist = Double.infinity
    let k = poly.count
    for i in 0..<k {
        let (ax, az) = (poly[i].x, poly[i].z)
        let b = i + 1 < k ? poly[i + 1] : poly[0]
        let dx = b.x - ax, dz = b.z - az
        let rx = px - ax, rz = pz - az
        if dx * rz - dz * rx < 0.0 { inside = false }
        let d2 = dx * dx + dz * dz
        let t = d2 <= 0.0 ? 0.0 : max(0.0, min(1.0, (rx * dx + rz * dz) / d2))
        let d = hypot(px - (ax + t * dx), pz - (az + t * dz))
        if d < dist { dist = d }
    }
    return inside ? dist : -dist
}

/// Intersection of segment p->q with the infinite line a->b (they cross by
/// construction, so the denominator is non-zero).
private func lineIsect(_ p: Pt, _ q: Pt, _ a: Pt, _ b: Pt) -> Pt {
    let r = (q.x - p.x, q.z - p.z)
    let s = (b.x - a.x, b.z - a.z)
    let denom = r.0 * s.1 - r.1 * s.0
    let t = ((a.x - p.x) * s.1 - (a.z - p.z) * s.0) / denom
    return Pt(x: p.x + t * r.0, z: p.z + t * r.1)
}

/// Drop points coincident with their predecessor (and the wrap-around duplicate) —
/// Sutherland–Hodgman emits those for degenerate subjects.
private func dedupe(_ poly: [Pt]) -> [Pt] {
    var out: [Pt] = []
    for p in poly {
        if out.isEmpty || hypot(p.x - out[out.count - 1].x, p.z - out[out.count - 1].z) > 1e-12 {
            out.append(p)
        }
    }
    if out.count > 1,
       hypot(out[0].x - out[out.count - 1].x, out[0].z - out[out.count - 1].z) <= 1e-12 {
        out.removeLast()
    }
    return out
}

/// Sutherland–Hodgman: `subject` clipped by convex CCW `clipper`.
///
/// `subject` may be degenerate (a 2-point segment or a single point): the wrap-around
/// edge list handles both, so edge/corner contacts clip correctly. A degenerate
/// *clipper* (a supporter whose own top contact is an edge or a corner) has no
/// interior to clip against, so the patch is just the subject points lying on it —
/// zero area either way.
private func clip(_ subject: [Pt], _ clipper: [Pt]) -> [Pt] {
    if subject.isEmpty || clipper.isEmpty { return [] }
    if clipper.count < 3 { return subject.filter { signedDist($0, clipper) >= -touchTolM } }
    var out = subject
    let k = clipper.count
    for i in 0..<k {
        if out.isEmpty { return [] }
        // Edge order is the clip order and the clip order shapes the output polygon,
        // so it stays (clipper[i] -> clipper[i+1]), wrapping at the end.
        let a = clipper[i]
        let b = i + 1 < k ? clipper[i + 1] : clipper[0]
        let (ax, az) = (a.x, a.z)
        // cross2(a, b, p) == ex * (p.z - az) - ez * (p.x - ax)
        let ex = b.x - ax, ez = b.z - az
        if out.allSatisfy({ ex * ($0.z - az) - ez * ($0.x - ax) >= 0.0 }) {
            continue  // nothing to cut (the common "sits well inside" case)
        }
        var nxt: [Pt] = []
        var prev = out[out.count - 1]
        var prevIn = ex * (prev.z - az) - ez * (prev.x - ax) >= 0.0
        for cur in out {
            let curIn = ex * (cur.z - az) - ez * (cur.x - ax) >= 0.0
            if curIn != prevIn { nxt.append(lineIsect(prev, cur, a, b)) }
            if curIn { nxt.append(cur) }
            prev = cur; prevIn = curIn
        }
        out = dedupe(nxt)
    }
    return out
}

/// (xMin, xMax, zMin, zMax) if `hull` is an axis-aligned rectangle (exactly two
/// distinct x and two distinct z values), else nil.
///
/// For such a clipper, "every subject point is inside" is exactly "the subject's XZ
/// bounding box is inside this rectangle", which is how the caller skips `clip` for
/// the overwhelmingly common object-sitting-well-inside-the-floor case.
private func axisRect(_ poly: [Pt]) -> (Double, Double, Double, Double)? {
    guard poly.count == 4 else { return nil }
    var xs: [Double] = [], zs: [Double] = []
    for p in poly {
        if !xs.contains(p.x) { xs.append(p.x) }
        if !zs.contains(p.z) { zs.append(p.z) }
    }
    guard xs.count == 2, zs.count == 2 else { return nil }
    return (min(xs[0], xs[1]), max(xs[0], xs[1]), min(zs[0], zs[1]), max(zs[0], zs[1]))
}

/// Compute a `SupportResult` per object in `scene.objects` (same order).
///
/// Assumes the scene is already valid (non-colliding, contained) — this function does
/// not re-check that.
///
/// `epsilon` (meters) is the contact tolerance, used both for "is this object resting
/// on that one" (gap between an object's lowest Y and the supporting floor/object's
/// highest Y) and for which corners count as touching a contact plane. Deliberately
/// larger (1e-3 = 1mm) than Collision/Containment's 1e-6 float-noise epsilon — this
/// one has to absorb real reconstruction/measurement noise between two independently
/// placed objects' faces, not just float64 rounding.
///
/// `geom` is the shared `SceneGeometry`; pass the one you already built and it is
/// reused as-is, otherwise it is precomputed here.
public func checkSupport(
    _ scene: Scene,
    epsilon: Double = 1e-3,
    floatingThreshold: Double = 0.05,
    geom: SceneGeometry? = nil
) throws -> [SupportResult] {
    let g = try geom ?? precompute(scene)
    let n = g.n
    // The container's 4 lowest corners (ties broken by index, so the SET is the
    // same one numpy's argsort picks).
    let lowest = (0..<g.containerVertices.count).sorted { a, b in
        let (ya, yb) = (g.containerVertices[a].y, g.containerVertices[b].y)
        return ya == yb ? a < b : ya < yb
    }.prefix(4)
    let floorHull = hull(lowest.map { Pt(x: g.containerVertices[$0].x, z: g.containerVertices[$0].z) })

    // Contact masks for every object, packed to a per-object corner bitmask so the
    // face fast path is a dictionary lookup.
    var lowBits = [Int](repeating: 0, count: n)
    var highBits = [Int](repeating: 0, count: n)
    var xzAll: [[Pt]] = []
    xzAll.reserveCapacity(n)
    for i in 0..<n {
        let loY = g.aabbMin[i].y + epsilon
        let hiY = g.aabbMax[i].y - epsilon
        var lo = 0, hi = 0
        var xz: [Pt] = []
        xz.reserveCapacity(8)
        for k in 0..<8 {
            let v = g.vertices[i][k]
            if v.y <= loY { lo |= 1 << k }
            if v.y >= hiY { hi |= 1 << k }
            xz.append(Pt(x: v.x, z: v.z))
        }
        lowBits[i] = lo
        highBits[i] = hi
        xzAll.append(xz)
    }

    var supporters = [[Int]](repeating: [], count: n)
    var supporting = Set<Int>()  // objects something actually rests on
    for pair in restingPairs(g, contactEps: epsilon) {
        supporters[pair.top].append(pair.bottom)
        supporting.insert(pair.bottom)
    }

    var bottomPts: [[Pt]] = []
    var bottomHulls: [[Pt]] = []
    // Top footprints are only ever read for objects that support something, so the
    // rest stay nil (in a flat layout that is every object).
    var topHulls = [[Pt]?](repeating: nil, count: n)
    for (i, xz) in xzAll.enumerated() {
        let pts = bitIndices(lowBits[i]).map { xz[$0] }
        bottomPts.append(pts)
        bottomHulls.append(faceHull(xz, faceCycles[lowBits[i]]) ?? hull(pts))
        if supporting.contains(i) {
            let hiBits = highBits[i]
            topHulls[i] = faceHull(xz, faceCycles[hiBits]) ?? hull(bitIndices(hiBits).map { xz[$0] })
        }
    }

    // Scanned prisms: redo those rows off their own ring vertices. Boxes never
    // enter this loop, so their code path above is untouched.
    for i in 0..<n where g.isPrism[i] {
        let loY = g.aabbMin[i].y + epsilon
        let hiY = g.aabbMax[i].y - epsilon
        let ring = g.prismVerts[i]
        let pts = ring.filter { $0.y <= loY }.map { Pt(x: $0.x, z: $0.z) }
        bottomPts[i] = pts
        bottomHulls[i] = hull(pts)
        if supporting.contains(i) {
            topHulls[i] = hull(ring.filter { $0.y >= hiY }.map { Pt(x: $0.x, z: $0.z) })
        }
    }

    let floored = onFloor(g, contactEps: epsilon)
    // COM projection: the OBB center for a box, the footprint centroid for a
    // scanned prism -- `SceneGeometry.com`, not the OBB center directly.
    let centersXZ = g.com.map { Pt(x: $0.x, z: $0.z) }
    // Objects whose whole XZ footprint is inside a rectangular floor: their bottom
    // footprint survives the floor clip untouched (see `axisRect`).
    var insideFloor = [Bool](repeating: false, count: n)
    if let rect = axisRect(floorHull) {
        let (fx0, fx1, fz0, fz1) = rect
        for i in 0..<n {
            insideFloor[i] = g.aabbMin[i].x >= fx0 && g.aabbMax[i].x <= fx1
                && g.aabbMin[i].z >= fz0 && g.aabbMax[i].z <= fz1
        }
    }

    var results: [SupportResult] = []
    results.reserveCapacity(n)
    for i in 0..<n {
        let bottomHull = bottomHulls[i]
        let bottomArea = polyArea(bottomHull)
        let degenerate = bottomArea < degenerateAreaM2

        // floor first, then scene order; the flag is "the clip cannot cut this"
        var candidates: [(name: String, top: [Pt], uncut: Bool)] =
            floored[i] ? [("container_floor", floorHull, insideFloor[i])] : []
        candidates += supporters[i].sorted().map { (g.ids[$0], topHulls[$0]!, false) }

        var names: [String] = []
        var patchAreas: [String: Double] = [:]
        var patchVertices: [Pt] = []
        var covered = 0.0
        var pointSupported = degenerate ? [Bool](repeating: false, count: bottomPts[i].count) : []
        for candidate in candidates {
            let patch = candidate.uncut ? bottomHull : clip(bottomHull, candidate.top)
            if patch.isEmpty { continue }
            names.append(candidate.name)
            // An uncut patch IS the bottom footprint, area included.
            let area = candidate.uncut ? bottomArea : polyArea(patch)
            patchAreas[candidate.name] = area
            patchVertices += patch
            covered += area
            if degenerate {
                for (k, p) in bottomPts[i].enumerated() {
                    if !pointSupported[k] && signedDist(p, candidate.top) >= -touchTolM {
                        pointSupported[k] = true
                    }
                }
            }
        }

        let supportRatio: Double
        if degenerate {
            supportRatio = pointSupported.isEmpty
                ? 0.0
                : Double(pointSupported.filter { $0 }.count) / Double(pointSupported.count)
        } else {
            supportRatio = min(1.0, covered / bottomArea)
        }

        let supportPolygon: [Pt]
        if names.count == 1 && patchVertices == bottomHull {
            // Uncut single patch: the bottom footprint is already a canonical hull
            // (CCW, no collinear points, starts at its lex-min vertex), so
            // re-hulling it would return it unchanged.
            supportPolygon = bottomHull
        } else {
            supportPolygon = hull(patchVertices)
        }
        var margin: Double
        if !supportPolygon.isEmpty {
            margin = signedDist(centersXZ[i], supportPolygon)
            if -touchTolM < margin && margin < 0.0 {
                margin = 0.0  // COM on the boundary counts as supported
            }
        } else {
            margin = floatingMarginSentinelM
        }

        results.append(SupportResult(
            objectId: g.ids[i],
            supportRatio: supportRatio,
            stabilityMarginM: margin,
            supportingObjects: names,
            floating: supportRatio < floatingThreshold,
            unstable: margin < 0,
            supportedByUnstable: false,
            contactPolygon: supportPolygon.map { [$0.x, $0.z] },
            patchAreasM2: patchAreas
        ))
    }

    // Chain instability, bottom-up: a supporter's bottom is always below its
    // supportee's, so ascending aabbMin.y visits supporters first.
    let order = (0..<n).sorted { a, b in
        let (ya, yb) = (g.aabbMin[a].y, g.aabbMin[b].y)
        return ya == yb ? a < b : ya < yb
    }
    for i in order {
        var flag = false
        for s in results[i].supportingObjects where s != "container_floor" {
            let j = g.index[s]!
            if results[j].floating || results[j].unstable || results[j].supportedByUnstable {
                flag = true
                break
            }
        }
        results[i].supportedByUnstable = flag
    }
    return results
}
