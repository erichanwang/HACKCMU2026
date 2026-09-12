// Top-level physics validation entry point. Port of physics/validator.py.
//
// One call runs the whole deterministic pipeline on a candidate layout:
//
//     precompute (SceneGeometry.swift)     one pass: OBBs, vertices, AABBs
//       -> containment  (per-wall depths, per-wall compressibility)
//       -> collision    (AABB broad phase + 15-axis SAT)
//       -> support      (exact contact polygons, static stability, chains)
//       -> constraints  (travel metadata, transitive load)
//       -> metrics      (COM, fill, clearances -- solver objective terms)
//
// This is the only function the packing solver / renderer needs to call. For a
// solver inner loop ("can I add this one object?") use `PlacementValidator`
// (Incremental.swift) and call this once at the end.
//
// Never throws. Any `MalformedSceneError` (non-finite / non-positive dims,
// non-finite position, zero quaternion, duplicate ids) short-circuits to
// `{valid: false, score: 0, violations: [MALFORMED_GEOMETRY], warnings: [],
// metrics: {}}`.
//
// Determinism: no randomness; `violations` and `warnings` are each sorted by
// `(type, objectKey)` where a pair entry uses its lexicographically smaller id.
// Python's `list.sort` is stable, Swift's `sort` is not, so the original
// insertion index is the final tie-breaker here -- byte-identical ordering.
//
// Severity heuristics (each clamped to [0,1]; used only for `score`) and the
// exact per-entry field tables are documented in docs/PHYSICS.md §10.

import Foundation

/// Wall name suffix ("+x" / "-x" -> "x") -> container-local axis index.
private let wallAxisIndex: [Character: Int] = ["x": 0, "y": 1, "z": 2]

public let defaultFloatingThreshold = 0.05

@inline(__always) private func clamp01(_ x: Double) -> Double { max(0.0, min(1.0, x)) }

@inline(__always) private func minComponent(_ v: Vec3) -> Double { min(v.x, min(v.y, v.z)) }

/// The MALFORMED_GEOMETRY short-circuit result (`metrics` is an empty object,
/// matching Python v2's `"metrics": {}`).
private func malformedResult(_ objectId: String?, _ detail: String) -> ValidationResult {
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

/// Python `_sort_key`: `(type, object)`, or for a pair entry the
/// lexicographically smaller of `objects`.
private func sortKey(_ entry: JSONValue) -> (String, String) {
    let type = entry["type"]?.stringValue ?? ""
    if let oid = entry["object"]?.stringValue { return (type, oid) }
    if let objs = entry["objects"]?.arrayValue, !objs.isEmpty {
        return (type, objs.compactMap { $0.stringValue }.min() ?? "")
    }
    return (type, "")
}

/// Stable sort by `sortKey` -- equal keys keep insertion order, like Python.
private func sortedEntries(_ entries: [JSONValue]) -> [JSONValue] {
    entries.enumerated().sorted { l, r in
        let (a, b) = (sortKey(l.element), sortKey(r.element))
        return a == b ? l.offset < r.offset : a < b
    }.map(\.element)
}

/// Run the full physics validation pipeline on `scene`. Never throws.
public func validateLayout(_ scene: Scene,
                           floatingThreshold: Double = defaultFloatingThreshold) -> ValidationResult {
    do {
        return try pipeline(scene, floatingThreshold: floatingThreshold)
    } catch let e as MalformedSceneError {
        return malformedResult(e.objectId, e.message)
    } catch {
        // Defensive: anything geometry raises that isn't tagged with an id.
        return malformedResult(nil, "\(error)")
    }
}

private func pipeline(_ scene: Scene, floatingThreshold: Double) throws -> ValidationResult {
    let geom = try precompute(scene)
    let objs = geom.objects
    let n = max(1, geom.n)
    var violations: [JSONValue] = []
    var warnings: [JSONValue] = []
    let container = geom.containerOBB

    // --- Containment (per-wall compressibility) ---
    let containerDenom = max(1e-9, minComponent(container.halfExtents))
    for res in try checkSceneContainment(scene, geom: geom) {
        let i = geom.index[res.objectId]!
        let obj = objs[i]
        let rawDepth = res.penetrationDepthM
        var effective: [String: Double] = [:]
        if obj.rigidity == .rigid {
            effective = res.perWallDepthM  // rigid -> allowance 0
        } else {
            for (wall, depth) in res.perWallDepthM {
                let axis = container.axes.column(wallAxisIndex[wall.last!]!)
                let allowance = containerWallAllowanceM(
                    obj, wallAxisExtentM: axisProjectedExtentM(geom.obbs[i], axis: axis))
                effective[wall] = max(0.0, depth - allowance)
            }
        }
        let effectiveDepth = effective.values.max() ?? 0.0
        if effectiveDepth <= 0.0 {
            if rawDepth > 0.0 {
                warnings.append(.object([
                    "type": "SOFT_COMPRESSION",
                    "object": .string(res.objectId),
                    "raw_penetration_depth_m": .number(rawDepth),
                    "compressed_depth_m": .number(rawDepth),
                    "walls": .strings(res.perWallDepthM.keys.sorted()),
                ]))
            }
            continue
        }
        var perWall: [String: JSONValue] = [:]
        for (w, d) in effective where d > 0.0 { perWall[w] = .number(d) }
        violations.append(.object([
            "type": "CONTAINER_PENETRATION",
            "object": .string(res.objectId),
            "penetration_depth_m": .number(effectiveDepth),
            "raw_penetration_depth_m": .number(rawDepth),
            "compressed_depth_m": .number(rawDepth - effectiveDepth),
            "violated_walls": .strings(perWall.keys.sorted()),
            "per_wall_depth_m": .object(perWall),
            "penetrating_vertices": .array(res.penetratingVertices.map { .vec($0) }),
            "severity": .number(clamp01(effectiveDepth / containerDenom)),
        ]))
    }

    // --- Collisions (broad phase + SAT; only colliding pairs come back) ---
    for r in collideScene(geom) {
        let i = geom.index[r.aId]!, j = geom.index[r.bId]!
        let a = objs[i], b = objs[j]
        let rawDepth = r.penetrationDepthM
        let mtvAxis = r.axis ?? Vec3(0, 0, 0)  // always set when colliding
        let contactPoint = r.contactPoint ?? Vec3(0, 0, 0)
        let allowance: Double
        if a.rigidity == .rigid && b.rigidity == .rigid {
            allowance = 0.0
        } else {
            allowance = combinedCollisionAllowanceM(
                a, b, mtvAxis: mtvAxis, obbA: geom.obbs[i], obbB: geom.obbs[j])
        }
        let effectiveDepth = max(0.0, rawDepth - allowance)
        let pair = [a.id, b.id].sorted()
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
        let denom = max(1e-9, min(minComponent(a.dimensions), minComponent(b.dimensions)))
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

    // --- Support / static stability ---
    for s in try checkSupport(scene, floatingThreshold: floatingThreshold, geom: geom) {
        let center = geom.obbs[geom.index[s.objectId]!].center
        let comXZ = JSONValue.array([.number(center.x), .number(center.z)])
        if s.floating {
            violations.append(.object([
                "type": "UNSUPPORTED_OBJECT",
                "object": .string(s.objectId),
                "support_ratio": .number(s.supportRatio),
                "center_of_mass_projection": comXZ,
                "severity": .number(clamp01(1.0 - s.supportRatio / floatingThreshold)),
            ]))
        } else if s.unstable {
            warnings.append(.object([
                "type": "UNSTABLE_STACK",
                "object": .string(s.objectId),
                "stability_margin_m": .number(s.stabilityMarginM),
                "support_ratio": .number(s.supportRatio),
                "supporting_objects": .strings(s.supportingObjects),
                "center_of_mass_projection": comXZ,
                "contact_polygon": .array(s.contactPolygon.map { p in .array(p.map { .number($0) }) }),
            ]))
        } else if s.supportedByUnstable {
            warnings.append(.object([
                "type": "UNSTABLE_SUPPORT_CHAIN",
                "object": .string(s.objectId),
                "supporting_objects": .strings(s.supportingObjects),
            ]))
        }
    }

    // --- Travel constraints (transitive load) ---
    var massById: [String: Double] = [:]
    for o in objs { massById[o.id] = o.massKg }
    let (cViolations, cWarnings) = try checkConstraints(scene, geom: geom)
    for v in cViolations {
        var entry: [String: JSONValue] = ["type": .string(v.type), "object": .string(v.objectId)]
        for (k, val) in v.details { entry[k] = val }
        if v.type == "FRAGILE_OBJECT_OVERLOADED" {
            let weight = v.details["supported_weight_kg"]?.doubleValue ?? 0.0
            entry["severity"] = .number(clamp01(weight / max(0.1, massById[v.objectId] ?? 0.1)))
        } else {  // LIQUID_NOT_UPRIGHT / INVALID_ORIENTATION
            let tilt = v.details["tilt_deg"]?.doubleValue ?? 0.0
            let tol = v.details["tolerance_deg"]?.doubleValue ?? 15.0
            entry["severity"] = .number(clamp01((tilt - tol) / max(1e-9, 90.0 - tol)))
        }
        violations.append(.object(entry))
    }
    for w in cWarnings {
        var entry: [String: JSONValue] = ["type": .string(w.type), "object": .string(w.objectId)]
        for (k, val) in w.details { entry[k] = val }
        warnings.append(.object(entry))
    }

    violations = sortedEntries(violations)
    warnings = sortedEntries(warnings)
    // Summed in sorted order, like Python (float addition is not associative).
    var severitySum = 0.0
    for v in violations { severitySum += min(1.0, v["severity"]?.doubleValue ?? 1.0) }

    return ValidationResult(
        valid: violations.isEmpty,
        score: max(0.0, 1.0 - severitySum / Double(n)),
        violations: violations,
        warnings: warnings,
        metrics: sceneMetrics(geom)
    )
}

/// `validate(scene, placements)`: apply `placements` (if given), then
/// `validateLayout`. Never throws -- an unknown placement id comes back as the
/// single MALFORMED_GEOMETRY violation naming the first bad id, instead of
/// propagating `applyPlacements`'s error. Mirrors `physics.io.validate`.
public func validate(_ scene: Scene, placements: [Placement]? = nil) -> ValidationResult {
    guard let placements = placements else { return validateLayout(scene) }
    do {
        return validateLayout(try applyPlacements(scene, placements))
    } catch let e as MalformedSceneError {
        return malformedResult(e.objectId, e.message)
    } catch {
        return malformedResult(nil, "\(error)")
    }
}
