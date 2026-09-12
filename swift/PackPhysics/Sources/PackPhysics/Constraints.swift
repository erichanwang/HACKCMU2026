// Travel-semantics constraint checker. Mirrors physics/constraints.py.
//
// Optional metadata-driven layer on top of `SceneGeometry`. Does nothing if
// every object's `Constraints` is left at defaults (all false / nil) -- it
// only flags things an object opted into via `fragile`, `keepUpright`,
// `cannotSupportWeight`, `heavy`, or `orientationLock`.
//
// Checks:
//   1. keepUpright: local Y axis (`axes.column(1)`) must stay within
//      `angleTolDeg` (default 15) of world up (0, 1, 0). -> LIQUID_NOT_UPRIGHT.
//   2. orientationLock:
//        "this_side_up" -> same test as keepUpright.
//        "flat_only"    -> local Y axis must be within tolerance of +Y or -Y
//                          (object lying on its largest face, either way up).
//        "horizontal"   -> local Y axis must be within tolerance of the XZ
//                          plane (roughly perpendicular to world up).
//      -> INVALID_ORIENTATION.
//   3. cannotSupportWeight: violated if nonzero TRANSITIVE load rests on top
//      of the object (below). -> FRAGILE_OBJECT_OVERLOADED.
//   4. fragile (warning, not a violation): nonzero transitive load rests on
//      top of the object, OR the object's XZ footprint overlaps a `heavy`
//      object's footprint at roughly the same height. -> FRAGILE_LOAD.
//   5. heavy resting on top of anything (informational, always emitted when
//      it happens, independent of the object underneath's constraints).
//      -> HEAVY_ON_TOP.
//
// Contact topology ("what rests on what") comes from the shared
// `restingPairs`: an ordered (top, bottom, xzOverlapArea) triple for every
// pair where top's lowest-Y is within `contactEpsM` of bottom's highest-Y
// and their AABB footprints overlap with positive area.
//
// Load model -- transitive, area-weighted propagation: a resting DAG can
// stack more than one level deep (a shoe on a toiletry bag on a laptop): the
// laptop is loaded by the *whole* stack above it, not just the toiletry bag
// directly touching it. For each object x:
//
//     load[x] = mass[x] + sum_{y rests on x} load[y] * w_yx
//     w_yx = area_yx / sum_{z: y rests on z} area_yz
//     supportedWeightKg[x] = load[x] - mass[x]
//
// i.e. each object's accumulated load (its own mass plus everything piled on
// it) is split among *its own* direct supporters in proportion to contact
// overlap area. Computed by processing objects top-down (descending
// `aabbMin.y`, so a resting DAG's leaves are visited before its roots) and,
// for each object in that order, pushing its already-fully-accumulated
// `load` down onto its own direct supporters. `directWeightKg` (sum of mass
// of objects directly on top, no propagation) is kept alongside the
// transitive figure since it is still a useful "what's touching this" number
// for a renderer.
//
// `HEAVY_ON_TOP.details.load_path`: from the heavy object down to the
// floor-level (no-supporters) object, following the heaviest-loaded
// supporting edge at each step -- i.e. at each node, the direct supporter
// receiving the largest share of that node's load (equivalent to the
// largest-area supporter, since a node's load is fixed when comparing its
// own supporters). Cycle-guarded.

import Foundation

private let worldUp = Vec3(0, 1, 0)

private func degrees(_ radians: Double) -> Double { radians * 180.0 / Double.pi }

public struct ConstraintViolation: Equatable {
    public var type: String
    public var objectId: String
    public var details: [String: JSONValue]

    public init(type: String, objectId: String, details: [String: JSONValue] = [:]) {
        self.type = type
        self.objectId = objectId
        self.details = details
    }
}

public struct ConstraintWarning: Equatable {
    public var type: String
    public var objectId: String
    public var details: [String: JSONValue]

    public init(type: String, objectId: String, details: [String: JSONValue] = [:]) {
        self.type = type
        self.objectId = objectId
        self.details = details
    }
}

/// Clipped cosine of the angle between each object's local up axis
/// (`axes.column(1)`) and world up, for every object at once. Mirrors Python
/// `_up_axis_cosines`: dividing by the (already ~1) norm is kept anyway so
/// this is bit-for-bit identical to the per-object formula, which only the
/// caller (for objects that opted into an orientation constraint) turns into
/// degrees via `acos`.
private func upAxisCosines(_ g: SceneGeometry) -> [Double] {
    g.obbs.map { obb in
        let up = obb.axes.column(1)
        return min(1.0, max(-1.0, dot(up, worldUp) / length(up)))
    }
}

public func checkConstraints(
    _ scene: Scene,
    angleTolDeg: Double = 15.0,
    contactEpsM: Double = 0.02,
    geom: SceneGeometry? = nil
) throws -> (violations: [ConstraintViolation], warnings: [ConstraintWarning]) {
    var violations: [ConstraintViolation] = []
    var warnings: [ConstraintWarning] = []

    let g = try geom ?? precompute(scene)
    let n = g.n
    if n == 0 { return (violations, warnings) }

    let objects = g.objects
    let ids = g.ids
    let masses = g.masses
    let pairs = restingPairs(g, contactEps: contactEpsM)

    var directWeight = [Double](repeating: 0.0, count: n)          // sum of mass of objects DIRECTLY on top of x
    var restingOnDirect = [[Int]](repeating: [], count: n)          // top -> [direct supporter idx, ...]
    var supporters = [[(bottom: Int, area: Double)]](repeating: [], count: n)  // top -> [(bottom idx, area), ...]
    var totalSupportArea = [Double](repeating: 0.0, count: n)       // per top object, sum of area over its own direct supporters

    for (top, bottom, area) in pairs {
        directWeight[bottom] += masses[top]
        restingOnDirect[top].append(bottom)
        supporters[top].append((bottom, area))
        totalSupportArea[top] += area
    }

    // Transitive load: visit top-down (highest aabbMin.y first) so that by
    // the time a node is visited, everything resting on it has already
    // pushed its share into it -- then push this node's now-final load onto
    // its own direct supporters, split by area fraction.
    var load = masses
    let order = (0..<n).sorted { a, b in
        let ya = g.aabbMin[a].y, yb = g.aabbMin[b].y
        return ya != yb ? ya > yb : a < b
    }
    for y in order {
        let areaTotal = totalSupportArea[y]
        if areaTotal <= 0.0 { continue }
        for (x, area) in supporters[y] {
            load[x] += load[y] * (area / areaTotal)
        }
    }

    let supportedWeight = zip(load, masses).map { $0 - $1 }

    func loadPath(_ start: Int) -> [String] {
        var path = [ids[start]]
        var seen: Set<Int> = [start]
        var cur = start
        while !supporters[cur].isEmpty {
            var best = supporters[cur][0]
            for s in supporters[cur].dropFirst() where s.area > best.area {
                best = s
            }
            let nxt = best.bottom
            if seen.contains(nxt) { break }
            path.append(ids[nxt])
            seen.insert(nxt)
            cur = nxt
        }
        return path
    }

    let cosUp = upAxisCosines(g)
    let heavyIdx = (0..<n).filter { objects[$0].constraints.heavy }

    for i in 0..<n {
        let obj = objects[i]
        let c = obj.constraints
        let lock = c.orientationLock
        // Only orientation constraints read the tilt, so only they pay for it.
        let tilt = (c.keepUpright || lock != nil) ? degrees(acos(cosUp[i])) : 0.0

        if c.keepUpright && tilt > angleTolDeg {
            violations.append(ConstraintViolation(
                type: "LIQUID_NOT_UPRIGHT",
                objectId: obj.id,
                details: ["tilt_deg": .number(tilt), "tolerance_deg": .number(angleTolDeg)]
            ))
        }

        switch lock {
        case "this_side_up":
            if tilt > angleTolDeg {
                violations.append(ConstraintViolation(
                    type: "INVALID_ORIENTATION",
                    objectId: obj.id,
                    details: ["lock": .string(lock!), "tilt_deg": .number(tilt), "tolerance_deg": .number(angleTolDeg)]
                ))
            }
        case "flat_only":
            let angleToAxis = min(tilt, 180.0 - tilt)  // distance to nearer of +Y/-Y
            if angleToAxis > angleTolDeg {
                violations.append(ConstraintViolation(
                    type: "INVALID_ORIENTATION",
                    objectId: obj.id,
                    details: ["lock": .string(lock!), "tilt_deg": .number(angleToAxis), "tolerance_deg": .number(angleTolDeg)]
                ))
            }
        case "horizontal":
            let angleToPlane = abs(90.0 - tilt)  // distance from the XZ plane (90 deg from up)
            if angleToPlane > angleTolDeg {
                violations.append(ConstraintViolation(
                    type: "INVALID_ORIENTATION",
                    objectId: obj.id,
                    details: ["lock": .string(lock!), "tilt_deg": .number(angleToPlane), "tolerance_deg": .number(angleTolDeg)]
                ))
            }
        default:
            break
        }

        if c.cannotSupportWeight && supportedWeight[i] > 0 {
            violations.append(ConstraintViolation(
                type: "FRAGILE_OBJECT_OVERLOADED",
                objectId: obj.id,
                details: [
                    "supported_weight_kg": .number(supportedWeight[i]),
                    "direct_weight_kg": .number(directWeight[i]),
                ]
            ))
        }

        if c.fragile {
            let adjacentHeavy = heavyIdx.contains { j in j != i && xzOverlapArea(g, i, j) > 0.0 }
            if supportedWeight[i] > 0 || adjacentHeavy {
                warnings.append(ConstraintWarning(
                    type: "FRAGILE_LOAD",
                    objectId: obj.id,
                    details: [
                        "supported_weight_kg": .number(supportedWeight[i]),
                        "direct_weight_kg": .number(directWeight[i]),
                        "adjacent_heavy": .bool(adjacentHeavy),
                    ]
                ))
            }
        }

        if c.heavy && !restingOnDirect[i].isEmpty {
            warnings.append(ConstraintWarning(
                type: "HEAVY_ON_TOP",
                objectId: obj.id,
                details: [
                    "resting_on": .strings(restingOnDirect[i].map { ids[$0] }),
                    "load_path": .strings(loadPath(i)),
                ]
            ))
        }
    }

    return (violations, warnings)
}
