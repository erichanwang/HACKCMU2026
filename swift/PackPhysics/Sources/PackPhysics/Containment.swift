// Object-in-container containment checks. Mirrors physics/containment.py.
//
// Assumptions (same as the Python layer):
// - Both container and object are convex OBBs. Non-box geometry is not modeled.
// - Coordinates match Schema.swift: X=right, Y=up, Z=forward, meters.
// - The container may itself be rotated/positioned arbitrarily in world space.
// - Containment is tested by projecting all 8 world-space vertices of the
//   object OBB into the container's local frame and comparing against the
//   container's half-extents on each local axis -- checking only the
//   object's center is NOT sufficient (a rotation can poke a corner through
//   a wall while the center stays well inside).
// - `epsilon` (meters) is a wall-touch tolerance: a vertex within `epsilon`
//   of a wall counts as inside/touching, not penetrating.
//
// Only object-vs-container is checked here; object-vs-object collision is a
// separate module's job. Soft/compressible allowance is not applied here
// either (see Compressibility.swift) -- this reports the RAW geometric depth.

import Foundation

// container-local axis index -> (negative wall name, positive wall name),
// flattened in the same order as the Python `_WALL_ORDER`.
private let wallOrder = ["-x", "+x", "-y", "+y", "-z", "+z"]

public struct ContainmentResult: Equatable {
    public var objectId: String
    public var contained: Bool
    public var penetratingVertices: [Vec3]
    public var penetrationDepthM: Double
    public var violatedWalls: [String]
    public var perWallDepthM: [String: Double]

    public init(objectId: String, contained: Bool, penetratingVertices: [Vec3],
                penetrationDepthM: Double, violatedWalls: [String], perWallDepthM: [String: Double]) {
        self.objectId = objectId
        self.contained = contained
        self.penetratingVertices = penetratingVertices
        self.penetrationDepthM = penetrationDepthM
        self.violatedWalls = violatedWalls
        self.perWallDepthM = perWallDepthM
    }
}

/// Shared containment core: projects `verts` (8 world-space corners) into the
/// container's local frame and pulls out, per of the 3 container axes, the
/// max overshoot on each of the 6 walls plus which vertices penetrate any
/// wall. One object at a time -- `checkContainment` and
/// `checkSceneContainment` both loop this over n objects (n=1 for the former).
private func containmentArrays(
    verts: [Vec3], containerCenter: Vec3, containerAxes: Mat3, containerHalfExtents: Vec3, epsilon: Double
) -> (wallDepth: [Double], vertexPenetrating: [Bool]) {
    var wallDepth = [Double](repeating: -Double.infinity, count: 6)
    var vertexPenetrating = [Bool](repeating: false, count: verts.count)
    for (v, vert) in verts.enumerated() {
        let local = containerAxes.transposeMultiply(vert - containerCenter)
        for axis in 0..<3 {
            let over = abs(local[axis]) - containerHalfExtents[axis]
            let violated = over > epsilon
            if violated {
                vertexPenetrating[v] = true
                let posSide = local[axis] > 0
                let wall = 2 * axis + (posSide ? 1 : 0)
                if over > wallDepth[wall] { wallDepth[wall] = over }
            }
        }
    }
    return (wallDepth, vertexPenetrating)
}

private func buildResult(objectId: String, verts: [Vec3], wallDepth: [Double], vertexPenetrating: [Bool]) -> ContainmentResult {
    var perWall: [String: Double] = [:]
    for w in 0..<6 where wallDepth[w].isFinite {
        perWall[wallOrder[w]] = wallDepth[w]
    }
    let penetratingVertices = (0..<verts.count).filter { vertexPenetrating[$0] }.map { verts[$0] }
    return ContainmentResult(
        objectId: objectId,
        contained: penetratingVertices.isEmpty,
        penetratingVertices: penetratingVertices,
        penetrationDepthM: perWall.values.max() ?? 0.0,
        violatedWalls: perWall.keys.sorted(),
        perWallDepthM: perWall
    )
}

/// Is `object` fully inside `container`? A vertex up to `epsilon` meters
/// outside a wall still counts as contained (touching); beyond that is a
/// penetration.
public func checkContainment(container: OBB, object: OBB, epsilon: Double = 1e-6) -> ContainmentResult {
    let verts = obbVertices(object)
    let (wallDepth, vertexPenetrating) = containmentArrays(
        verts: verts, containerCenter: container.center, containerAxes: container.axes,
        containerHalfExtents: container.halfExtents, epsilon: epsilon
    )
    return buildResult(objectId: object.id, verts: verts, wallDepth: wallDepth, vertexPenetrating: vertexPenetrating)
}

/// Containment for every object in `scene`. Returns ONLY the results for
/// objects that are NOT contained (violations) -- a filtered list, not "all
/// results". An empty list means every object is fully contained (including
/// the 0-object scene). `geom`, when supplied, is used instead of running
/// `precompute` again.
public func checkSceneContainment(_ scene: Scene, epsilon: Double = 1e-6, geom: SceneGeometry? = nil) throws -> [ContainmentResult] {
    let g = try geom ?? precompute(scene)
    let container = g.containerOBB
    var results: [ContainmentResult] = []
    for i in 0..<g.n {
        let (wallDepth, vertexPenetrating) = containmentArrays(
            verts: g.vertices[i], containerCenter: container.center, containerAxes: container.axes,
            containerHalfExtents: container.halfExtents, epsilon: epsilon
        )
        if vertexPenetrating.contains(true) {
            results.append(buildResult(objectId: g.ids[i], verts: g.vertices[i], wallDepth: wallDepth, vertexPenetrating: vertexPenetrating))
        }
    }
    return results
}
