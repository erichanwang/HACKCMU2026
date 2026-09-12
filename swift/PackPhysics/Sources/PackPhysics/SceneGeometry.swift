// Per-scene precomputed geometry, built ONCE per validation and shared by every
// check. Mirrors physics/scene_geometry.py: object order is `scene.objects`
// order; `index[id]` maps an id to its row.

import Foundation

public struct SceneGeometry {
    public let scene: Scene
    public let objects: [SceneObject]
    public let ids: [String]
    public let index: [String: Int]
    public let containerOBB: OBB
    public let containerVertices: [Vec3]  // 8
    public let containerFloorY: Double  // min world-Y of the container OBB
    public let obbs: [OBB]
    public let vertices: [[Vec3]]  // n x 8, SIGNS order (the box envelope)
    public let aabbMin: [Vec3]  // from the prism vertices (== OBB corners for boxes)
    public let aabbMax: [Vec3]
    public let masses: [Double]
    // Scanned-footprint support. Boxes are 4-point prisms, so every consumer can
    // treat all objects uniformly: footprints[i] is the CCW local (x, z) polygon;
    // prismVerts[i] is the (2m) world points, bottom ring then top ring; isPrism[i]
    // is true only when the object supplied a footprint; yawOnly[i] is true when
    // the object's local y axis is world +-Y (the prism's side faces are then
    // vertical and its footprint is exact in XZ).
    public let footprints: [[FootprintPoint]]
    public let prismVerts: [[Vec3]]
    public let isPrism: [Bool]
    public let yawOnly: [Bool]
    // World center of mass under uniform density: the OBB center for boxes, the
    // footprint's area centroid (at mid-height) mapped to world for prisms. Use
    // this -- not `obbs[i].center` -- for COM projections and mass-weighted metrics.
    public let com: [Vec3]

    public var n: Int { objects.count }
}

/// Raise if any object id repeats or equals the container id.
public func checkNoDuplicateIds(_ scene: Scene) throws {
    var seen: Set<String> = [scene.container.id]
    for o in scene.objects {
        if seen.contains(o.id) {
            throw MalformedSceneError(objectId: o.id, message: "duplicate object id: '\(o.id)'")
        }
        seen.insert(o.id)
    }
}

/// Build all shared arrays. Throws `MalformedSceneError` (duplicate ids,
/// non-finite / non-positive dims, non-finite position, bad quaternion).
public func precompute(_ scene: Scene) throws -> SceneGeometry {
    try checkNoDuplicateIds(scene)
    let cobb = try obbFrom(scene.container)
    let cverts = obbVertices(cobb)
    let floorY = cverts.map(\.y).min() ?? 0
    var obbs: [OBB] = []
    var verts: [[Vec3]] = []
    var mins: [Vec3] = []
    var maxs: [Vec3] = []
    obbs.reserveCapacity(scene.objects.count)
    for o in scene.objects {
        // Same family as the non-finite dimension/position checks in `obbFrom`:
        // a NaN/inf mass would otherwise flow into `sceneMetrics` and the
        // constraints load model and surface as a non-finite number in the
        // result, which `JSONEncoder` refuses to encode (`resultToJSON` throws).
        guard o.massKg.isFinite else {
            throw MalformedSceneError(objectId: o.id, message: "\(o.id): invalid mass_kg \(o.massKg)")
        }
        let obb = try obbFrom(o)
        let v = obbVertices(obb)
        obbs.append(obb)
        verts.append(v)
        var lo = v[0], hi = v[0]
        for p in v.dropFirst() {
            lo = pointwiseMin(lo, p)
            hi = pointwiseMax(hi, p)
        }
        mins.append(lo)
        maxs.append(hi)
    }
    let ids = scene.objects.map(\.id)
    var index: [String: Int] = [:]
    for (i, id) in ids.enumerated() { index[id] = i }

    // Footprints / prisms. Boxes get their 4 rectangle corners, so the AABB of
    // the prism vertices equals the AABB of the OBB corners for them; scanned
    // prisms get a tighter AABB than their box envelope.
    var footprints: [[FootprintPoint]] = []
    var prisms: [[Vec3]] = []
    var isPrism: [Bool] = []
    footprints.reserveCapacity(scene.objects.count)
    prisms.reserveCapacity(scene.objects.count)
    isPrism.reserveCapacity(scene.objects.count)
    for (i, o) in scene.objects.enumerated() {
        let fp = try footprintLocal(id: o.id, dimensions: o.dimensions, footprint: o.footprint)
        footprints.append(fp)
        prisms.append(prismVertices(obbs[i], fp))
        isPrism.append(o.footprint != nil)
    }
    // Boxes keep the bit-identical AABB of their 8 OBB corners (the snapshot
    // regression guard depends on it); only scanned prisms use their ring.
    var yawOnly: [Bool] = []
    var com: [Vec3] = []
    yawOnly.reserveCapacity(scene.objects.count)
    com.reserveCapacity(scene.objects.count)
    for i in 0..<scene.objects.count {
        if isPrism[i] {
            var lo = prisms[i][0], hi = prisms[i][0]
            for p in prisms[i].dropFirst() { lo = pointwiseMin(lo, p); hi = pointwiseMax(hi, p) }
            mins[i] = lo
            maxs[i] = hi
        }
        yawOnly.append(abs(obbs[i].axes.c1.y) > 1.0 - 1e-9)
        if isPrism[i] {
            let c2 = polygonCentroid2D(footprints[i])
            com.append(obbs[i].center + obbs[i].axes * Vec3(c2.x, 0.0, c2.z))
        } else {
            com.append(obbs[i].center)
        }
    }

    return SceneGeometry(
        scene: scene, objects: scene.objects, ids: ids, index: index,
        containerOBB: cobb, containerVertices: cverts, containerFloorY: floorY,
        obbs: obbs, vertices: verts, aabbMin: mins, aabbMax: maxs,
        masses: scene.objects.map(\.massKg),
        footprints: footprints, prismVerts: prisms, isPrism: isPrism, yawOnly: yawOnly, com: com
    )
}

/// All (i, j), i < j, whose world AABBs overlap (padded by `epsilon`). The cheap
/// necessary condition before any SAT narrow phase. O(n^2), tiny constants.
public func aabbCandidatePairs(_ g: SceneGeometry, epsilon: Double = 0.0) -> [(Int, Int)] {
    var out: [(Int, Int)] = []
    let n = g.n
    guard n >= 2 else { return out }
    for i in 0..<(n - 1) {
        let loI = g.aabbMin[i], hiI = g.aabbMax[i]
        for j in (i + 1)..<n {
            let loJ = g.aabbMin[j], hiJ = g.aabbMax[j]
            if hiI.x + epsilon >= loJ.x, hiJ.x + epsilon >= loI.x,
               hiI.y + epsilon >= loJ.y, hiJ.y + epsilon >= loI.y,
               hiI.z + epsilon >= loJ.z, hiJ.z + epsilon >= loI.z {
                out.append((i, j))
            }
        }
    }
    return out
}

/// Overlap area of objects i and j's XZ axis-aligned footprints (from their
/// AABBs); 0 when disjoint. Exact for yaw-only rotation, conservative otherwise.
/// This is the shared *topology* test (touching or not); modules needing exact
/// rotated footprints clip polygons themselves.
public func xzOverlapArea(_ g: SceneGeometry, _ i: Int, _ j: Int) -> Double {
    let dx = min(g.aabbMax[i].x, g.aabbMax[j].x) - max(g.aabbMin[i].x, g.aabbMin[j].x)
    let dz = min(g.aabbMax[i].z, g.aabbMax[j].z) - max(g.aabbMin[i].z, g.aabbMin[j].z)
    if dx <= 0 || dz <= 0 { return 0 }
    return dx * dz
}

/// Unified "what rests on what" graph: (top, bottom, xzOverlapArea) for every
/// ordered pair where top's lowest Y is within `contactEps` of bottom's highest Y
/// and their XZ footprints overlap with positive area. Same iteration order as
/// Python (`np.nonzero` row-major over [top, bottom]).
public func restingPairs(_ g: SceneGeometry, contactEps: Double) -> [(top: Int, bottom: Int, area: Double)] {
    var out: [(top: Int, bottom: Int, area: Double)] = []
    let n = g.n
    guard n >= 2 else { return out }
    for t in 0..<n {
        let bottomY = g.aabbMin[t].y
        for b in 0..<n where b != t {
            if abs(bottomY - g.aabbMax[b].y) <= contactEps {
                let area = xzOverlapArea(g, t, b)
                if area > 0 { out.append((t, b, area)) }
            }
        }
    }
    return out
}

/// Per object: is its lowest Y within `contactEps` of the container floor plane?
public func onFloor(_ g: SceneGeometry, contactEps: Double) -> [Bool] {
    g.aabbMin.map { abs($0.y - g.containerFloorY) <= contactEps }
}
