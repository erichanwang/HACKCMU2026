// OBB-vs-OBB collision via the Separating Axis Theorem. Port of physics/collision.py.
//
// Assumptions
// -----------
// Both boxes are convex OBBs (`OBB`): a center, 3 orthonormal world axes (columns of
// `axes`) and 3 half-extents. Boxes only -- an OBB is a conservative proxy for whatever
// mesh it stands for. Coordinates are meters, X=right / Y=up / Z=forward (right-handed);
// SAT itself is coordinate-free, the units only fix `penetrationDepthM`.
//
// Two convex polyhedra are separated iff some separating axis exists among the 3 face
// normals of A, the 3 face normals of B, and the 9 cross products of A's edges with B's
// edges (Gottschalk et al.). For boxes the face normals are the boxes' own axes. 15 axes
// total, checked in a FIXED order (A's axes, B's axes, then the 9 crosses i-major), so
// ties in the "smallest overlap" search keep whichever axis was checked first -- the same
// tie-break as the Python reference.
//
// The math (Ericson, *Real-Time Collision Detection* section 4.4.1 / Gottschalk's OBBTree)
// ------------------------------------------------------------------------------
// Everything lives in A's frame, so no candidate axis is ever built or normalized
// explicitly (only the single winning MTV axis is, at the end). With `a`/`b` the
// half-extent vectors:
//
//     R    = A.axesᵀ · B.axes      R[i][j] = a_i · b_j
//     t    = A.axesᵀ (B.c - A.c)   B's center in A's frame
//     absR = |R| (+ epsParallel on the cross-axis terms only, see below)
//
// - Face axis a_i: separated iff |t_i| > a_i + Σ_j b_j·absR[i][j].
// - Face axis b_j: separated iff |Σ_i t_i·R[i][j]| > b_j + Σ_i a_i·absR[i][j].
// - Cross axis a_i × b_j (9 of them), all quantities implicitly scaled by
//   ‖a_i × b_j‖ = sqrt(max(0, 1 - R[i][j]²)):
//       ra   = a_{i+1}·absR[i+2][j] + a_{i+2}·absR[i+1][j]
//       rb   = b_{j+1}·absR[i][j+2] + b_{j+2}·absR[i][j+1]
//       proj = |t_{i+2}·R[i+1][j] - t_{i+1}·R[i+2][j]|          (indices mod 3)
//   separated iff proj > ra + rb.
//
// `epsParallel` (1e-8) pads the `absR` entries used by the CROSS-axis radii only. Those
// radii are sums of products of half-extents with `R` entries that all vanish exactly when
// a_i ∥ b_j, so for near-parallel edge pairs `ra + rb` collapses into float noise while
// `proj` is a difference of two nearly-equal products -- the comparison becomes meaningless
// and can invent a separation. The padding makes the test *conservative* there (it may
// report a collision for boxes separated only along a near-degenerate axis) instead of
// skipping such axes. The 6 face radii are NOT padded: they are O(half-extent), never
// suffer that cancellation, and padding them would perturb every face-axis penetration
// depth (the common case) by ~1e-8 · Σ half-extents.
//
// Epsilon semantics
// -----------------
// `epsilon` is distance-space slack on the *overlap along the current axis*, in meters --
// not on the SAT separation test that decides collision. Per axis the overlap is
// `radiusSum - centerDistance`; for the 9 cross axes that difference comes out scaled by
// ‖a_i × b_j‖, so it is divided by that norm to put all 15 overlaps in meters before any
// comparison. If ANY axis has `overlap <= epsilon` the boxes are NOT colliding (separated,
// exactly touching, or overlapping by a sliver thinner than epsilon) and the result carries
// depth 0 with `axis`/`contactPoint` nil. Exactly touching boxes (overlap == 0) therefore
// report `colliding == false` for any epsilon >= 0. When every one of the 15 axes has
// `overlap > epsilon`, the result is colliding with `penetrationDepthM` = the minimum such
// overlap (the MTV magnitude) and `axis` its unit direction. So a razor-thin real overlap
// (1e-4 m) at epsilon 1e-6 still reports colliding, while numerically-touching boxes
// (overlap ~1e-9 of float error) correctly report clear.
//
// Degenerate handling
// -------------------
// A cross axis whose norm is <= 1e-8 (genuinely parallel edges) is excluded from the MTV
// search, since its overlap cannot be converted to meters without dividing by ~0. It is
// excluded from the collision decision too, which costs nothing: under the padded test such
// an axis can never be the separating one (`proj` is exactly 0 for parallel edges while
// `ra + rb >= epsParallel · ... > 0`). If the winning cross axis somehow has zero norm the
// world direction falls back to the zero vector, matching the Python reference.
//
// Complexity
// ----------
// `checkCollision`: O(1) -- 15 fixed axes, O(1) each, no vertices, one axis built.
// `aabbOverlap`: O(1) -- 8 vertices per box, one bbox each.
// `checkPairs`: O(m) over the given pairs (no numpy here, so no batching: a plain loop is
// faster than any array machinery at this size).
// `collideScene`: O(n²) broad phase (`aabbCandidatePairs`) plus the narrow phase over the
// survivors. No spatial index; at packing scale (n <= ~40) the O(n²) sweep is microseconds.
//
// Known failure modes
// -------------------
// - Boxes only: concave shapes are not modeled; an OBB may report a collision the true mesh
//   would not (or miss one).
// - Near-parallel edges: padded, not skipped (see above). Two boxes separated *only* by such
//   a degenerate axis can be misreported as colliding with a small depth. The bias is
//   deliberate: for a packing validator a false "colliding" is conservative, a false "clear"
//   is not.
// - Large coordinate magnitudes: Double has ~15-17 significant digits, so the sub-millimeter
//   precision aimed at here degrades around 1e6 m. Keep scenes near the origin.
// - `contactPoint` is a documented approximation, not a true contact manifold.

import Foundation

/// Padding added to the cross-axis `absR` terms (never the face terms).
private let epsParallel = 1e-8
/// Below this, a_i × b_j is degenerate: no metric depth, excluded from the MTV search.
private let crossAxisMinNorm = 1e-8

public struct CollisionResult: Equatable {
    public var colliding: Bool
    /// MTV magnitude in meters; 0 when not colliding.
    public var penetrationDepthM: Double
    /// Unit MTV direction, oriented A -> B. Nil when not colliding.
    public var axis: Vec3?
    /// Approximation: the midpoint, along `axis`, of the overlap interval of the two boxes'
    /// projections onto it. A reasonable single point inside the overlap region, NOT a
    /// contact manifold (face-face contact is really a polygon). Nil when not colliding.
    public var contactPoint: Vec3?
    public var aId: String
    public var bId: String
    /// The true XZ contact patch as [[x, z], ...] -- only the prism narrow phase
    /// fills it (the OBB path has no polygon to report).
    public var contactPolygon: [[Double]]?
    /// Which narrow phase produced this: "sat_obb" | "sat_prism" | "sat_obb_envelope".
    public var narrowPhase: String
    /// True when the shapes were approximated (box envelopes for a tilted prism).
    public var approximate: Bool

    public init(colliding: Bool, penetrationDepthM: Double = 0.0, axis: Vec3? = nil,
                contactPoint: Vec3? = nil, aId: String, bId: String,
                contactPolygon: [[Double]]? = nil, narrowPhase: String = "sat_obb",
                approximate: Bool = false) {
        self.colliding = colliding
        self.penetrationDepthM = penetrationDepthM
        self.axis = axis
        self.contactPoint = contactPoint
        self.aId = aId
        self.bId = bId
        self.contactPolygon = contactPolygon
        self.narrowPhase = narrowPhase
        self.approximate = approximate
    }
}

@inline(__always) private func absVec(_ v: Vec3) -> Vec3 { Vec3(abs(v.x), abs(v.y), abs(v.z)) }

@inline(__always) private func absMat(_ m: Mat3) -> Mat3 {
    Mat3(columns: absVec(m.c0), absVec(m.c1), absVec(m.c2))
}

/// Cheap world-axis-aligned bounding-box overlap test, for broad-phase pruning.
///
/// Builds each OBB's world AABB from its 8 vertices and checks the 3 interval overlaps.
/// A *necessary* condition for OBB-OBB collision, not sufficient: false negatives are
/// impossible, but it can return true for non-colliding OBBs whose tight AABBs overlap.
/// Scene-level equivalent: `aabbCandidatePairs(geom, eps)`, which is what `collideScene`
/// uses; this per-pair helper is for callers holding only two OBBs.
public func aabbOverlap(_ a: OBB, _ b: OBB) -> Bool {
    func bounds(_ v: [Vec3]) -> (Vec3, Vec3) {
        var lo = v[0], hi = v[0]
        for p in v.dropFirst() { lo = pointwiseMin(lo, p); hi = pointwiseMax(hi, p) }
        return (lo, hi)
    }
    let (aMin, aMax) = bounds(obbVertices(a))
    let (bMin, bMax) = bounds(obbVertices(b))
    return aMax.x >= bMin.x && aMax.y >= bMin.y && aMax.z >= bMin.z
        && bMax.x >= aMin.x && bMax.y >= aMin.y && bMax.z >= aMin.z
}

/// SAT test for two OBBs; see the file header for the formulation and epsilon semantics.
///
/// `axis` is the unit minimum-translation-vector direction: of the 15 candidates, the one
/// with the smallest metric overlap -- the direction SAT would push along to separate the
/// boxes with least motion -- oriented from A's center towards B's.
public func checkCollision(_ a: OBB, _ b: OBB, epsilon: Double = 1e-6) -> CollisionResult {
    let aax = a.axes, bax = b.axes
    let ha = a.halfExtents, hb = b.halfExtents

    // R[i][j] = a_i . b_j, stored column-major so R[i, j] reads naturally.
    let R = Mat3(columns: aax.transposeMultiply(bax.c0),
                 aax.transposeMultiply(bax.c1),
                 aax.transposeMultiply(bax.c2))
    let d = b.center - a.center
    let t = aax.transposeMultiply(d)  // B's center in A's frame
    let absR = absMat(R)
    let pad = Vec3(repeating: epsParallel)
    let absRp = Mat3(columns: absR.c0 + pad, absR.c1 + pad, absR.c2 + pad)

    // 6 face axes: the axes are unit vectors, so the overlaps are already in meters.
    // `absR * hb` is row_i(absR) . hb; `transposeMultiply` is column_j . v.
    let ovlA = ha + absR * hb - absVec(t)
    let ovlB = hb + absR.transposeMultiply(ha) - absVec(R.transposeMultiply(t))

    // MTV = smallest metric overlap. Strict `<` keeps the first-checked axis on ties, so the
    // winner matches numpy's `argmin` over [A faces, B faces, crosses i-major].
    var bestDepth = Double.infinity
    var bestK = 0
    for k in 0..<3 where ovlA[k] < bestDepth { bestDepth = ovlA[k]; bestK = k }
    for k in 0..<3 where ovlB[k] < bestDepth { bestDepth = ovlB[k]; bestK = 3 + k }
    for i in 0..<3 {
        let i1 = (i + 1) % 3, i2 = (i + 2) % 3
        for j in 0..<3 {
            let rij = R[i, j]
            let norm = max(0.0, 1.0 - rij * rij).squareRoot()  // ||a_i x b_j||
            if norm <= crossAxisMinNorm { continue }  // parallel edges: no metric depth
            let j1 = (j + 1) % 3, j2 = (j + 2) % 3
            let ra = ha[i1] * absRp[i2, j] + ha[i2] * absRp[i1, j]
            let rb = hb[j1] * absRp[i, j2] + hb[j2] * absRp[i, j1]
            let proj = abs(t[i2] * R[i1, j] - t[i1] * R[i2, j])
            let depth = (ra + rb - proj) / norm
            if depth < bestDepth { bestDepth = depth; bestK = 6 + 3 * i + j }
        }
    }

    // Any axis with overlap <= epsilon separates the boxes.
    guard bestDepth > epsilon else {
        return CollisionResult(colliding: false, aId: a.id, bId: b.id)
    }

    // World direction of the winning axis only (never all 9 crosses).
    var axis: Vec3
    if bestK < 3 {
        axis = aax.column(bestK)
    } else if bestK < 6 {
        axis = bax.column(bestK - 3)
    } else {
        let kc = bestK - 6
        let c = cross(aax.column(kc / 3), bax.column(kc % 3))
        let cn = length(c)
        axis = cn > 0.0 ? c / cn : c
    }
    if dot(axis, d) < 0.0 { axis = axis * -1.0 }  // orient A -> B

    // Approximate contact point: midpoint of the overlap interval on `axis`, keeping the
    // perpendicular component of the centers' midpoint.
    let raW = dot(absVec(aax.transposeMultiply(axis)), ha)
    let rbW = dot(absVec(bax.transposeMultiply(axis)), hb)
    let aC = dot(axis, a.center)
    let bC = dot(axis, b.center)
    let mid = (max(aC - raW, bC - rbW) + min(aC + raW, bC + rbW)) / 2.0
    let perpRef = (a.center + b.center) / 2.0
    let contact = perpRef + (mid - dot(axis, perpRef)) * axis

    return CollisionResult(colliding: true, penetrationDepthM: bestDepth, axis: axis,
                           contactPoint: contact, aId: a.id, bId: b.id)
}

/// SAT for every `(i, j)` of `pairs`, indexing `g.obbs`. One result per input pair, in input
/// order, with `aId`/`bId` from `g.ids`. Same math and epsilon semantics as
/// `checkCollision` -- literally the same code path.
public func checkPairs(_ g: SceneGeometry, _ pairs: [(Int, Int)],
                       epsilon: Double = 1e-6) -> [CollisionResult] {
    pairs.map { checkCollision(g.obbs[$0.0], g.obbs[$0.1], epsilon: epsilon) }
}

/// Unit outward edge normals of a CCW convex polygon (p -> q, d = q - p gives
/// outward normal (d.z, -d.x)). Mirrors `physics.collision._outward_normals_2d`.
private func outwardNormals2D(_ poly: [FootprintPoint]) -> [FootprintPoint] {
    let n = poly.count
    var out: [FootprintPoint] = []
    out.reserveCapacity(n)
    for i in 0..<n {
        let p = poly[i], q = poly[(i + 1) % n]
        let dx = q.x - p.x, dz = q.z - p.z
        var nx = dz, nz = -dx
        let len = (nx * nx + nz * nz).squareRoot()
        if len > 0.0 { nx /= len; nz /= len }
        out.append(FootprintPoint(x: nx, z: nz))
    }
    return out
}

/// Object i's bottom prism ring in world XZ (CCW) plus its unit outward edge
/// normals. Only valid for a `yawOnly` object. Mirrors `physics.collision._prism_xz`;
/// `cache` (per object index) is worth passing whenever one object is tested
/// against several others (`collideScene`'s prism sweep does).
private func prismXZ(_ g: SceneGeometry, _ i: Int,
                     _ cache: inout [Int: ([FootprintPoint], [FootprintPoint])]) -> ([FootprintPoint], [FootprintPoint]) {
    if let cached = cache[i] { return cached }
    let m = g.footprints[i].count
    var ring = (0..<m).map { FootprintPoint(x: g.prismVerts[i][$0].x, z: g.prismVerts[i][$0].z) }
    if g.obbs[i].axes.c1.y < 0.0 { ring.reverse() }
    let result = (ring, outwardNormals2D(ring))
    cache[i] = result
    return result
}

/// Sutherland-Hodgman: `poly` clipped by every half-plane of the CCW convex
/// polygon `clip`. Used only for the yaw-only prism narrow phase's contact
/// patch -- mirrors `physics.collision._clip_convex_2d` (plainer / stricter
/// than `Geometry.swift`'s `convexClip2D`: `>= 0.0`, no epsilon slack).
private func clipConvex2D(_ poly: [FootprintPoint], _ clip: [FootprintPoint]) -> [FootprintPoint] {
    var out = poly
    let m = clip.count
    for k in 0..<m {
        if out.count < 3 { return [] }
        let a = clip[k], b = clip[(k + 1) % m]
        let ex = b.x - a.x, ez = b.z - a.z
        let s = out.map { ex * ($0.z - a.z) - ez * ($0.x - a.x) }
        if s.min()! >= 0.0 { continue }
        var kept: [FootprintPoint] = []
        let n = out.count
        for t in 0..<n {
            let u = (t + 1) % n
            if s[t] >= 0.0 { kept.append(out[t]) }
            if (s[t] >= 0.0) != (s[u] >= 0.0) {
                let f = s[t] / (s[t] - s[u])
                kept.append(FootprintPoint(x: out[t].x + (out[u].x - out[t].x) * f,
                                           z: out[t].z + (out[u].z - out[t].z) * f))
            }
        }
        out = kept
    }
    return out
}

/// Footprint-aware narrow phase for objects i and j of `g`. Mirrors
/// `physics.collision.check_prism_pair`.
///
/// Both `yawOnly` (the LiDAR case): exact, treating the prism as the Cartesian
/// product of its XZ footprint and its Y interval -- candidate axes are the
/// outward edge normals of both world-XZ footprints plus world Y, checked in
/// that order (ties keep whichever axis was checked first, as in the OBB
/// path). Either object tilted: falls back to the OBB-envelope SAT on the two
/// box envelopes (`approximate = true`) -- by the box-envelope invariant this
/// can only over-report, never miss a real collision.
private func checkPrismPair(_ g: SceneGeometry, _ i: Int, _ j: Int, _ epsilon: Double,
                            _ cache: inout [Int: ([FootprintPoint], [FootprintPoint])]) -> CollisionResult {
    guard g.yawOnly[i] && g.yawOnly[j] else {
        var res = checkCollision(g.obbs[i], g.obbs[j], epsilon: epsilon)
        res.narrowPhase = "sat_obb_envelope"
        res.approximate = true
        return res
    }
    let ids = g.ids
    let clear = CollisionResult(colliding: false, aId: ids[i], bId: ids[j], narrowPhase: "sat_prism")
    let yLo = max(g.aabbMin[i].y, g.aabbMin[j].y)
    let yHi = min(g.aabbMax[i].y, g.aabbMax[j].y)
    let yOvl = yHi - yLo
    if yOvl <= epsilon { return clear }  // two scalars decide it; skip the XZ polygons entirely

    let (pa, na) = prismXZ(g, i, &cache)
    let (pb, nb) = prismXZ(g, j, &cache)
    let axes2 = na + nb
    var ovlXZ = [Double](repeating: 0, count: axes2.count)
    for k in 0..<axes2.count {
        let ax = axes2[k]
        var minA = Double.infinity, maxA = -Double.infinity
        for p in pa { let v = p.x * ax.x + p.z * ax.z; minA = min(minA, v); maxA = max(maxA, v) }
        var minB = Double.infinity, maxB = -Double.infinity
        for p in pb { let v = p.x * ax.x + p.z * ax.z; minB = min(minB, v); maxB = max(maxB, v) }
        ovlXZ[k] = min(maxA, maxB) - max(minA, minB)
    }
    var k = 0
    for idx in 1..<ovlXZ.count where ovlXZ[idx] < ovlXZ[k] { k = idx }
    let depth = min(ovlXZ[k], yOvl)
    if depth <= epsilon { return clear }

    var axis = ovlXZ[k] <= yOvl ? Vec3(axes2[k].x, 0.0, axes2[k].z) : Vec3(0.0, 1.0, 0.0)
    let d = g.obbs[j].center - g.obbs[i].center
    if dot(axis, d) < 0.0 { axis = axis * -1.0 }  // orient A -> B, same rule as the OBB path

    let polygon = clipConvex2D(pa, pb)
    let cx: Double, cz: Double
    var contactPolygon: [[Double]]
    if polygon.count >= 3 && polygonArea2D(polygon) > 0.0 {
        let c = polygonCentroid2D(polygon)
        cx = c.x; cz = c.z
        contactPolygon = polygon.map { [$0.x, $0.z] }
    } else {  // knife-edge touch: no patch to report, keep a point between the two
        let ca = polygonCentroid2D(pa), cb = polygonCentroid2D(pb)
        cx = (ca.x + cb.x) / 2.0; cz = (ca.z + cb.z) / 2.0
        contactPolygon = []
    }
    let contact = Vec3(cx, (yLo + yHi) / 2.0, cz)
    return CollisionResult(colliding: true, penetrationDepthM: depth, axis: axis, contactPoint: contact,
                           aId: ids[i], bId: ids[j], contactPolygon: contactPolygon, narrowPhase: "sat_prism")
}

/// Every colliding object pair in a scene: AABB broad phase + SAT narrow phase.
///
/// `broadPhaseEpsilon` (default: `epsilon`) pads the broad-phase AABBs, so a pair can only
/// be pruned when it is further apart than the narrow phase's own slack -- no pair SAT would
/// call colliding is lost (for a prism the AABB is its own, tighter than its box envelope's,
/// which only prunes harder). Only colliding results are returned, in `aabbCandidatePairs`
/// order (ascending (i, j)).
///
/// Pairs where neither object is a prism go through `checkCollision` exactly as before
/// (byte-identical to the pre-footprint behaviour); pairs involving a prism go through
/// `checkPrismPair`.
public func collideScene(_ g: SceneGeometry, epsilon: Double = 1e-6,
                         broadPhaseEpsilon: Double? = nil) -> [CollisionResult] {
    let eps = broadPhaseEpsilon ?? epsilon
    let pairs = aabbCandidatePairs(g, epsilon: eps)
    guard g.isPrism.contains(true) else {
        return checkPairs(g, pairs, epsilon: epsilon).filter(\.colliding)
    }
    var cache: [Int: ([FootprintPoint], [FootprintPoint])] = [:]
    var out: [CollisionResult] = []
    out.reserveCapacity(pairs.count)
    for (i, j) in pairs {
        if g.isPrism[i] || g.isPrism[j] {
            out.append(checkPrismPair(g, i, j, epsilon, &cache))
        } else {
            out.append(checkCollision(g.obbs[i], g.obbs[j], epsilon: epsilon))
        }
    }
    return out.filter(\.colliding)
}
