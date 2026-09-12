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
    public let vertices: [[Vec3]]  // n x 8, SIGNS order
    public let aabbMin: [Vec3]
    public let aabbMax: [Vec3]
    public let masses: [Double]

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
    return SceneGeometry(
        scene: scene, objects: scene.objects, ids: ids, index: index,
        containerOBB: cobb, containerVertices: cverts, containerFloorY: floorY,
        obbs: obbs, vertices: verts, aabbMin: mins, aabbMax: maxs,
        masses: scene.objects.map(\.massKg)
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
