// Incremental, per-object placement validation for the packing solver's inner
// loop. Port of physics/incremental.py.
//
// `validateLayout` re-validates an ENTIRE scene (O(k²)) every time the solver
// asks "I have k objects placed, can I add this one here?". `PlacementValidator`
// keeps a small cache of the k already-committed objects and, on each query,
// evaluates only the ONE new object against that cache: O(k) per call (an AABB
// prefilter over the cached min/max arrays, then narrow-phase SAT only on
// survivors), growing its arrays in place on `place` / shrinking on `remove`
// instead of rebuilding a `SceneGeometry`.
//
// Contract
// --------
// `tryPlace(obj)` and `place(obj)` return a `ValidationResult` shaped like
// `validateLayout`'s -- same violation/warning types, dict shapes and severity
// formulas -- but describing ONLY `obj` against the currently-committed state.
// `metrics` is always an empty object (Python's incremental result has no
// `metrics` key at all). `score` uses n = 1: `max(0, 1 - Σ min(1, severity))`.
// Never throws: malformed geometry or a duplicate id becomes a
// MALFORMED_GEOMETRY violation, exactly like `validateLayout`.
//
// What is NOT re-checked: previously committed objects' mutual validity. That
// was checked when each was placed; re-deriving it per query is exactly the
// O(k²) cost this type exists to avoid. The one documented exception is a new
// object landing ON a committed one flipping that object's
// FRAGILE_OBJECT_OVERLOADED status -- that IS handled, reported against the
// SUPPORTER's id. Run `validateLayout(pv.toScene())` once at the end as the
// authoritative check.
//
// Divergences from `validateLayout` (unchanged from the Python module's own
// "Divergences" list):
// - Support footprint is the object's full XZ-AABB and its supporters' XZ-AABBs,
//   not exact contact polygons -- identical for axis/yaw-aligned boxes (the
//   packing case), an over-estimate for a rolled/pitched object.
// - `supportRatio`'s covered area is the SUM of the clipped supporter rects
//   (clamped to 1.0), not Support.swift's exact union: double-counts overlapping
//   supporters, which only ever pushes the ratio toward 1.0 (the conservative
//   direction for "yes, keep going"). ponytail: sum-of-clipped-rects,
//   O(#supporters); upgrade to the coordinate-compression union if overlapping
//   supporter stacks become common.
// - One `contactEps` (default 1e-3, Support.swift's default) covers BOTH "is
//   this resting on something" AND the fragile/heavy direct-contact rule, where
//   the full pipeline uses Constraints.swift's looser 0.02 for the latter --
//   slightly stricter here, and only for items resting with a 1mm-2cm gap.
// - Load is DIRECT only (the new object's own mass), not the full pipeline's
//   transitive, area-weighted propagation.
// - Orientation checks replicate Constraints.swift's angle math directly.
//
// Cache: per committed object its `SceneObject`, `OBB`, world vertices and AABB
// min/max, appended on `place` and dropped by index on `remove`.

import Foundation

/// (xMin, xMax, zMin, zMax)
private typealias Rect = (Double, Double, Double, Double)

private let worldUpVector = Vec3(0, 1, 0)
private let defaultAngleTolDeg = 15.0
private let wallAxisIdx: [Character: Int] = ["x": 0, "y": 1, "z": 2]

@inline(__always) private func clamp01(_ x: Double) -> Double { max(0.0, min(1.0, x)) }

@inline(__always) private func minComponent3(_ v: Vec3) -> Double { min(v.x, min(v.y, v.z)) }

private func rectIntersection(_ a: Rect, _ b: Rect) -> Rect? {
    let xMin = max(a.0, b.0), xMax = min(a.1, b.1)
    let zMin = max(a.2, b.2), zMax = min(a.3, b.3)
    if xMin >= xMax || zMin >= zMax { return nil }
    return (xMin, xMax, zMin, zMax)
}

private func rectArea(_ r: Rect) -> Double { max(0.0, r.1 - r.0) * max(0.0, r.3 - r.2) }

private func mergeRect(_ rects: [Rect]) -> Rect {
    (rects.map(\.0).min()!, rects.map(\.1).max()!, rects.map(\.2).min()!, rects.map(\.3).max()!)
}

/// + inside (distance to the nearest edge), - outside (distance to the rect).
private func signedDistToRect(_ px: Double, _ pz: Double, _ r: Rect) -> Double {
    let (xMin, xMax, zMin, zMax) = r
    if xMin <= px && px <= xMax && zMin <= pz && pz <= zMax {
        return min(min(px - xMin, xMax - px), min(pz - zMin, zMax - pz))
    }
    let dx = max(max(xMin - px, 0.0), px - xMax)
    let dz = max(max(zMin - pz, 0.0), pz - zMax)
    return -hypot(dx, dz)
}

private func rectsOverlap(_ a: Rect, _ b: Rect) -> Bool {
    a.0 < b.1 && b.0 < a.1 && a.2 < b.3 && b.2 < a.3
}

/// `(type, object)` / pair-entry sort key, and a stable sort over it -- Python's
/// `_sort_key` with the insertion index as the final tie-break.
private func incSortKey(_ entry: JSONValue) -> (String, String) {
    let type = entry["type"]?.stringValue ?? ""
    if let oid = entry["object"]?.stringValue { return (type, oid) }
    if let objs = entry["objects"]?.arrayValue, !objs.isEmpty {
        return (type, objs.compactMap { $0.stringValue }.min() ?? "")
    }
    return (type, "")
}

private func incSorted(_ entries: [JSONValue]) -> [JSONValue] {
    entries.enumerated().sorted { l, r in
        let (a, b) = (incSortKey(l.element), incSortKey(r.element))
        return a == b ? l.offset < r.offset : a < b
    }.map(\.element)
}

private func incMalformed(_ objectId: String?, _ detail: String) -> ValidationResult {
    ValidationResult(
        valid: false,
        score: 0.0,
        violations: [.object([
            "type": "MALFORMED_GEOMETRY",
            "object": objectId.map { JSONValue.string($0) } ?? .null,
            "detail": .string(detail),
        ])],
        warnings: [],
        metrics: .object([:])
    )
}

/// Incremental per-object validator for one `Container`. See the file header.
public final class PlacementValidator {
    public let container: Container
    public let epsilon: Double
    public let contactEps: Double
    public let floatingThreshold: Double

    private let containerOBB: OBB
    private let containerDenom: Double
    private let floorY: Double
    private let floorRect: Rect

    private var ids: [String] = []
    private var indexById: [String: Int] = [:]
    private var objectsById: [String: SceneObject] = [:]
    private var obbs: [OBB] = []
    private var aabbMin: [Vec3] = []
    private var aabbMax: [Vec3] = []

    /// Throws `MalformedSceneError` on a malformed container (Python's
    /// `obb_from` raises the same way from `__init__`).
    public init(container: Container, epsilon: Double = 1e-6, contactEps: Double = 1e-3,
                floatingThreshold: Double = 0.05) throws {
        self.container = container
        self.epsilon = epsilon
        self.contactEps = contactEps
        self.floatingThreshold = floatingThreshold
        containerOBB = try obbFrom(container)
        containerDenom = max(1e-9, minComponent3(containerOBB.halfExtents))
        let verts = obbVertices(containerOBB)
        floorY = verts.map(\.y).min()!
        floorRect = (verts.map(\.x).min()!, verts.map(\.x).max()!,
                     verts.map(\.z).min()!, verts.map(\.z).max()!)
    }

    public var placedIds: [String] { ids }

    public func toScene() -> Scene {
        Scene(container: container, objects: ids.map { objectsById[$0]! })
    }

    /// Evaluate `obj` against the committed state; commit nothing.
    public func tryPlace(_ obj: SceneObject) -> ValidationResult {
        evaluate(obj).0
    }

    /// Evaluate `obj` and commit it when valid (or when `force`).
    public func place(_ obj: SceneObject, force: Bool = false) -> ValidationResult {
        let (result, obb) = evaluate(obj)
        if let obb = obb, result.valid || force { commit(obj, obb) }
        return result
    }

    /// Drop a committed object from the cache (no-op if it was never placed).
    public func remove(id: String) {
        guard let idx = indexById[id] else { return }
        ids.remove(at: idx)
        objectsById[id] = nil
        obbs.remove(at: idx)
        aabbMin.remove(at: idx)
        aabbMax.remove(at: idx)
        indexById = [:]
        for (i, oid) in ids.enumerated() { indexById[oid] = i }
    }

    /// Cheap solver feedback for scoring a candidate position -- does NOT run
    /// the validation pipeline. Throws on malformed `obj` (Python raises
    /// `ValueError` here; unlike `tryPlace`, this is a raw metrics helper).
    ///
    /// `nearest_neighbor_gap_m`: Euclidean AABB gap to the nearest committed
    /// object -- 0.0 if AABBs overlap, null if nothing is committed yet.
    /// `wall_clearance_m`: min, over all 8 vertices and all 3 container wall
    /// axes, of half-extent minus |container-local coordinate| (negative if
    /// penetrating).
    public func incrementalMetrics(_ obj: SceneObject) throws -> JSONValue {
        let obb = try obbFrom(obj)
        let verts = obbVertices(obb)
        var objMin = verts[0], objMax = verts[0]
        for p in verts.dropFirst() {
            objMin = pointwiseMin(objMin, p)
            objMax = pointwiseMax(objMax, p)
        }

        var nnGap = JSONValue.null
        if !ids.isEmpty {
            var best = Double.infinity
            for i in 0..<ids.count {
                var g = Vec3(0, 0, 0)
                for k in 0..<3 {
                    g[k] = max(0.0, max(aabbMin[i][k] - objMax[k], objMin[k] - aabbMax[i][k]))
                }
                let d = (g.x * g.x + g.y * g.y + g.z * g.z).squareRoot()
                if d < best { best = d }
            }
            nnGap = .number(best)
        }

        var clearance = Double.infinity
        for v in verts {
            let local = containerOBB.axes.transposeMultiply(v - containerOBB.center)
            for k in 0..<3 {
                clearance = min(clearance, containerOBB.halfExtents[k] - abs(local[k]))
            }
        }

        return .object(["nearest_neighbor_gap_m": nnGap, "wall_clearance_m": .number(clearance)])
    }

    // MARK: - internals

    private func commit(_ obj: SceneObject, _ obb: OBB) {
        indexById[obj.id] = ids.count
        ids.append(obj.id)
        objectsById[obj.id] = obj
        obbs.append(obb)
        let verts = obbVertices(obb)
        var lo = verts[0], hi = verts[0]
        for p in verts.dropFirst() { lo = pointwiseMin(lo, p); hi = pointwiseMax(hi, p) }
        aabbMin.append(lo)
        aabbMax.append(hi)
    }

    private func xzRect(_ idx: Int) -> Rect {
        (aabbMin[idx].x, aabbMax[idx].x, aabbMin[idx].z, aabbMax[idx].z)
    }

    private func evaluate(_ obj: SceneObject) -> (ValidationResult, OBB?) {
        if indexById[obj.id] != nil {
            return (incMalformed(obj.id, "duplicate object id: '\(obj.id)'"), nil)
        }
        let obb: OBB
        do {
            obb = try obbFrom(obj)
        } catch let e as MalformedSceneError {
            return (incMalformed(obj.id, e.message), nil)
        } catch {
            return (incMalformed(obj.id, "\(error)"), nil)
        }

        var violations: [JSONValue] = []
        var warnings: [JSONValue] = []
        let verts = obbVertices(obb)
        var newMin = verts[0], newMax = verts[0]
        for p in verts.dropFirst() { newMin = pointwiseMin(newMin, p); newMax = pointwiseMax(newMax, p) }

        let (cv, cw) = containmentCheck(obj, obb)
        if let cv = cv { violations.append(cv) }
        if let cw = cw { warnings.append(cw) }

        let (collV, collW) = collisionChecks(obj, obb, newMin, newMax)
        violations += collV
        warnings += collW

        let (supportRatio, floating, stabilityWarning, supporters) = supportCheck(obj, obb, verts)
        if floating {
            violations.append(.object([
                "type": "UNSUPPORTED_OBJECT",
                "object": .string(obj.id),
                "support_ratio": .number(supportRatio),
                "severity": .number(clamp01(1.0 - supportRatio / floatingThreshold)),
            ]))
        } else if let stabilityWarning = stabilityWarning {
            warnings.append(stabilityWarning)
        }

        for sid in supporters {
            let supporter = objectsById[sid]!
            // Direct load only: the new object's own mass on each supporter.
            let weight = obj.massKg
            if supporter.constraints.cannotSupportWeight {
                violations.append(.object([
                    "type": "FRAGILE_OBJECT_OVERLOADED",
                    "object": .string(sid),
                    "supported_weight_kg": .number(weight),
                    "direct_weight_kg": .number(weight),
                    "severity": .number(clamp01(weight / max(0.1, supporter.massKg))),
                ]))
            }
            if supporter.constraints.fragile {
                warnings.append(.object([
                    "type": "FRAGILE_LOAD",
                    "object": .string(sid),
                    "supported_weight_kg": .number(weight),
                    "direct_weight_kg": .number(weight),
                    "adjacent_heavy": .bool(obj.constraints.heavy),
                ]))
            }
        }

        if obj.constraints.heavy && !supporters.isEmpty {
            warnings.append(.object([
                "type": "HEAVY_ON_TOP",
                "object": .string(obj.id),
                "resting_on": .strings(supporters),
            ]))
        }

        if obj.constraints.fragile {
            let ownRect: Rect = (newMin.x, newMax.x, newMin.z, newMax.z)
            let adjacentHeavy = ids.enumerated().contains { i, oid in
                objectsById[oid]!.constraints.heavy && rectsOverlap(ownRect, xzRect(i))
            }
            if adjacentHeavy {
                warnings.append(.object([
                    "type": "FRAGILE_LOAD",
                    "object": .string(obj.id),
                    "supported_weight_kg": .number(0.0),
                    "adjacent_heavy": .bool(true),
                ]))
            }
        }

        violations += orientationViolations(obj, obb)

        violations = incSorted(violations)
        warnings = incSorted(warnings)
        var severitySum = 0.0
        for v in violations { severitySum += min(1.0, v["severity"]?.doubleValue ?? 1.0) }

        let result = ValidationResult(
            valid: violations.isEmpty,
            score: max(0.0, 1.0 - severitySum),
            violations: violations,
            warnings: warnings,
            metrics: .object([:])
        )
        return (result, obb)
    }

    private func containmentCheck(_ obj: SceneObject, _ obb: OBB) -> (JSONValue?, JSONValue?) {
        let res = checkContainment(container: containerOBB, object: obb, epsilon: epsilon)
        if res.contained { return (nil, nil) }
        let rawDepth = res.penetrationDepthM
        // Per-wall allowance along that wall's axis (same rule as Validator.swift).
        var effective: [String: Double] = [:]
        if obj.rigidity == .rigid {
            effective = res.perWallDepthM
        } else {
            for (wall, depth) in res.perWallDepthM {
                let axis = containerOBB.axes.column(wallAxisIdx[wall.last!]!)
                let allowance = containerWallAllowanceM(
                    obj, wallAxisExtentM: axisProjectedExtentM(obb, axis: axis))
                effective[wall] = max(0.0, depth - allowance)
            }
        }
        let effectiveDepth = effective.values.max() ?? 0.0
        if effectiveDepth <= 0.0 {
            guard rawDepth > 0.0 else { return (nil, nil) }
            return (nil, .object([
                "type": "SOFT_COMPRESSION",
                "object": .string(obj.id),
                "raw_penetration_depth_m": .number(rawDepth),
                "compressed_depth_m": .number(rawDepth),
                "walls": .strings(res.perWallDepthM.keys.sorted()),
            ]))
        }
        var perWall: [String: JSONValue] = [:]
        for (w, d) in effective where d > 0.0 { perWall[w] = .number(d) }
        return (.object([
            "type": "CONTAINER_PENETRATION",
            "object": .string(obj.id),
            "penetration_depth_m": .number(effectiveDepth),
            "raw_penetration_depth_m": .number(rawDepth),
            "compressed_depth_m": .number(rawDepth - effectiveDepth),
            "violated_walls": .strings(perWall.keys.sorted()),
            "per_wall_depth_m": .object(perWall),
            "penetrating_vertices": .array(res.penetratingVertices.map { .vec($0) }),
            "severity": .number(clamp01(effectiveDepth / containerDenom)),
        ]), nil)
    }

    private func collisionChecks(_ obj: SceneObject, _ obb: OBB,
                                _ newMin: Vec3, _ newMax: Vec3) -> ([JSONValue], [JSONValue]) {
        var violations: [JSONValue] = []
        var warnings: [JSONValue] = []
        guard !ids.isEmpty else { return (violations, warnings) }

        // O(k) AABB prefilter (mirrors aabbOverlap, no epsilon slack --
        // checkCollision re-applies its own epsilon-aware margin on survivors).
        for idx in 0..<ids.count {
            let lo = aabbMin[idx], hi = aabbMax[idx]
            guard hi.x >= newMin.x, hi.y >= newMin.y, hi.z >= newMin.z,
                  newMax.x >= lo.x, newMax.y >= lo.y, newMax.z >= lo.z else { continue }
            let otherId = ids[idx]
            let other = objectsById[otherId]!
            let result = checkCollision(obb, obbs[idx], epsilon: epsilon)
            guard result.colliding else { continue }
            let rawDepth = result.penetrationDepthM
            let mtvAxis = result.axis ?? Vec3(0, 0, 0)
            let contactPoint = result.contactPoint ?? Vec3(0, 0, 0)
            let allowance: Double
            if obj.rigidity == .rigid && other.rigidity == .rigid {
                allowance = 0.0
            } else {
                allowance = combinedCollisionAllowanceM(
                    obj, other, mtvAxis: mtvAxis, obbA: obb, obbB: obbs[idx])
            }
            let effectiveDepth = max(0.0, rawDepth - allowance)
            let pair = [obj.id, otherId].sorted()
            if effectiveDepth <= 0.0 {
                if rawDepth > 0.0 {
                    warnings.append(.object([
                        "type": "SOFT_COMPRESSION",
                        "objects": .strings(pair),
                        "raw_penetration_depth_m": .number(rawDepth),
                        "compressed_depth_m": .number(rawDepth),
                        "contact_point": .vec(contactPoint),
                    ]))
                }
                continue
            }
            let denom = max(1e-9, min(minComponent3(obj.dimensions), minComponent3(other.dimensions)))
            violations.append(.object([
                "type": "OBJECT_COLLISION",
                "objects": .strings(pair),
                "penetration_depth_m": .number(effectiveDepth),
                "raw_penetration_depth_m": .number(rawDepth),
                "compressed_depth_m": .number(rawDepth - effectiveDepth),
                "contact_point": .vec(contactPoint),
                "axis": .vec(mtvAxis),
                "severity": .number(clamp01(effectiveDepth / denom)),
            ]))
        }
        return (violations, warnings)
    }

    /// (supportRatio, floating, stability warning, direct supporter ids).
    private func supportCheck(_ obj: SceneObject, _ obb: OBB,
                              _ verts: [Vec3]) -> (Double, Bool, JSONValue?, [String]) {
        let bottomY = verts.map(\.y).min()!
        let ownRect: Rect = (verts.map(\.x).min()!, verts.map(\.x).max()!,
                             verts.map(\.z).min()!, verts.map(\.z).max()!)
        let ownArea = rectArea(ownRect)

        var supportRects: [Rect] = []
        var supporters: [String] = []

        if abs(bottomY - floorY) <= contactEps, let clipped = rectIntersection(ownRect, floorRect) {
            supportRects.append(clipped)
        }
        for idx in 0..<ids.count where abs(aabbMax[idx].y - bottomY) <= contactEps {
            if let clipped = rectIntersection(ownRect, xzRect(idx)) {
                supportRects.append(clipped)
                supporters.append(ids[idx])
            }
        }

        var covered = 0.0
        for r in supportRects { covered += rectArea(r) }
        let supportRatio = ownArea <= 0.0 ? 0.0 : min(1.0, covered / ownArea)
        let floating = supportRatio < floatingThreshold

        var stabilityWarning: JSONValue? = nil
        if !floating && !supportRects.isEmpty {
            let margin = signedDistToRect(obb.center.x, obb.center.z, mergeRect(supportRects))
            if margin < 0 {
                stabilityWarning = .object([
                    "type": "UNSTABLE_STACK",
                    "object": .string(obj.id),
                    "stability_margin_m": .number(margin),
                    "center_of_mass_projection": .array([.number(obb.center.x), .number(obb.center.z)]),
                ])
            }
        }
        return (supportRatio, floating, stabilityWarning, supporters)
    }

    /// Constraints.swift's keepUpright / orientationLock angle math, replicated.
    private func orientationViolations(_ obj: SceneObject, _ obb: OBB) -> [JSONValue] {
        let c = obj.constraints
        let localUp = obb.axes.column(1)
        let cosine = min(1.0, max(-1.0, dot(localUp, worldUpVector) / length(localUp)))
        let tilt = acos(cosine) * 180.0 / Double.pi
        let tol = defaultAngleTolDeg
        let denom = max(1e-9, 90.0 - tol)

        func entry(_ type: String, _ tiltUsed: Double, lock: String? = nil) -> JSONValue {
            var d: [String: JSONValue] = [
                "type": .string(type),
                "object": .string(obj.id),
                "severity": .number(clamp01((tiltUsed - tol) / denom)),
                "tilt_deg": .number(tiltUsed),
                "tolerance_deg": .number(tol),
            ]
            if let lock = lock { d["lock"] = .string(lock) }
            return .object(d)
        }

        var out: [JSONValue] = []
        if c.keepUpright && tilt > tol { out.append(entry("LIQUID_NOT_UPRIGHT", tilt)) }

        switch c.orientationLock {
        case "this_side_up":
            if tilt > tol { out.append(entry("INVALID_ORIENTATION", tilt, lock: "this_side_up")) }
        case "flat_only":
            let angleToAxis = min(tilt, 180.0 - tilt)
            if angleToAxis > tol {
                out.append(entry("INVALID_ORIENTATION", angleToAxis, lock: "flat_only"))
            }
        case "horizontal":
            let angleToPlane = abs(90.0 - tilt)
            if angleToPlane > tol {
                out.append(entry("INVALID_ORIENTATION", angleToPlane, lock: "horizontal"))
            }
        default:
            break
        }
        return out
    }
}
