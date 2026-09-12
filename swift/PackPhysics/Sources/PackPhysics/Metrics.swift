// Scene-level packing metrics -- objective terms for the packing solver.
// Mirrors physics/metrics.py.
//
// `sceneMetrics(g)` takes an already-built `SceneGeometry` and returns a
// `JSONValue` matching the exact Python dictionary shape: overall mass
// balance (`total_mass_kg`, `center_of_mass`, `com_offset_m`), how full the
// container is (`fill_ratio`), and per-object spatial stats (`per_object`):
// wall clearance, nearest-neighbor gap/id, height off the floor, footprint,
// and volume.
//
// Units: meters and kilograms, matching Schema.swift.
//
// `com_offset_m` is expressed in the CONTAINER's local axes (not world), so a
// solver can push mass toward a fixed side of the container regardless of
// how the container itself is rotated/placed in world space -- same
// convention Containment.swift uses for wall projections.
//
// `nearest_neighbor_gap_m` is AABB-based (per axis `max(lo_j-hi_i, lo_i-hi_j)`,
// max over axes, clamped to >=0, then min over neighbours) -- conservative
// for rotated boxes. 0 when AABBs overlap. `null` when there is no other
// object to compare against.

import Foundation

public func sceneMetrics(_ g: SceneGeometry) -> JSONValue {
    let container = g.containerOBB
    let containerVolumeM3 = 8.0 * container.halfExtents.x * container.halfExtents.y * container.halfExtents.z
    let n = g.n

    if n == 0 {
        return .object([
            "total_mass_kg": .number(0.0),
            "center_of_mass": .vec(container.center),
            "com_offset_m": .vec(Vec3(0, 0, 0)),
            "fill_ratio": .number(0.0),
            "per_object": .object([:]),
        ])
    }

    let totalMassKg = g.masses.reduce(0.0, +)
    var weightedSum = Vec3(0, 0, 0)
    for i in 0..<n { weightedSum += g.masses[i] * g.obbs[i].center }
    // A massless scene (every `mass_kg` 0, or masses that cancel) has no
    // mass-weighted centroid. Fall back to the unweighted centroid -- the limit
    // as all masses become equal -- so `center_of_mass`/`com_offset_m` stay
    // finite: `JSONEncoder` throws on NaN, so without this a scene that
    // `validateLayout` happily accepts cannot be serialized by `resultToJSON`.
    // (Python divides unguarded and lets `json.dumps` emit a bare `NaN` token,
    // which is not valid JSON and no strict parser downstream can read back.)
    let centerOfMass = totalMassKg > 0
        ? weightedSum / totalMassKg
        : g.obbs.reduce(Vec3(0, 0, 0)) { $0 + $1.center } / Double(n)
    // world -> container-local, same convention as Containment.swift.
    let comOffsetM = container.axes.transposeMultiply(centerOfMass - container.center)

    var objectVolumesM3 = [Double](repeating: 0, count: n)
    for i in 0..<n {
        let he = g.obbs[i].halfExtents
        objectVolumesM3[i] = 8.0 * he.x * he.y * he.z
    }
    let fillRatio = objectVolumesM3.reduce(0.0, +) / containerVolumeM3

    // Wall clearance: same vertex-into-container-frame projection as
    // Containment.swift, but reported as remaining room (can go negative)
    // rather than filtered to violations only.
    var wallClearanceM = [Double](repeating: 0, count: n)
    for i in 0..<n {
        var minClear = Double.infinity
        for vert in g.vertices[i] {
            let local = container.axes.transposeMultiply(vert - container.center)
            for k in 0..<3 {
                let clear = container.halfExtents[k] - abs(local[k])
                if clear < minClear { minClear = clear }
            }
        }
        wallClearanceM[i] = minClear
    }

    // Nearest-neighbour AABB gap: O(n^2), min over neighbours (per object),
    // first index wins a tie (matches np.argmin).
    let hasNeighbour = n > 1
    var nearestGapM = [Double](repeating: 0, count: n)
    var nearestIdx = [Int](repeating: -1, count: n)
    if hasNeighbour {
        for i in 0..<n {
            var best = Double.infinity
            var bestJ = -1
            for j in 0..<n where j != i {
                var gapAxisMax = -Double.infinity
                for k in 0..<3 {
                    let loI = g.aabbMin[i][k], hiI = g.aabbMax[i][k]
                    let loJ = g.aabbMin[j][k], hiJ = g.aabbMax[j][k]
                    let m = max(loJ - hiI, loI - hiJ)
                    if m > gapAxisMax { gapAxisMax = m }
                }
                let gap = max(gapAxisMax, 0.0)
                if gap < best { best = gap; bestJ = j }
            }
            nearestGapM[i] = best
            nearestIdx[i] = bestJ
        }
    }

    var footprintAreaM2 = [Double](repeating: 0, count: n)
    var heightAboveFloorM = [Double](repeating: 0, count: n)
    for i in 0..<n {
        footprintAreaM2[i] = (g.aabbMax[i].x - g.aabbMin[i].x) * (g.aabbMax[i].z - g.aabbMin[i].z)
        heightAboveFloorM[i] = g.aabbMin[i].y - g.containerFloorY
    }

    var perObject: [String: JSONValue] = [:]
    for i in 0..<n {
        perObject[g.ids[i]] = .object([
            "wall_clearance_m": .number(wallClearanceM[i]),
            "nearest_neighbor_gap_m": hasNeighbour ? .number(nearestGapM[i]) : .null,
            "nearest_neighbor_id": hasNeighbour ? .string(g.ids[nearestIdx[i]]) : .null,
            "height_above_floor_m": .number(heightAboveFloorM[i]),
            "footprint_area_m2": .number(footprintAreaM2[i]),
            "volume_m3": .number(objectVolumesM3[i]),
        ])
    }

    return .object([
        "total_mass_kg": .number(totalMassKg),
        "center_of_mass": .vec(centerOfMass),
        "com_offset_m": .vec(comOffsetM),
        "fill_ratio": .number(fillRatio),
        "per_object": .object(perObject),
    ])
}
