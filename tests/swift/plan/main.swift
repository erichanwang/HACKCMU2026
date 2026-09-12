import Foundation
#if canImport(simd)
import simd
#endif

func approx(_ a: Float, _ b: Float, _ tol: Float = 1e-4) -> Bool { abs(a - b) < tol }
func approx(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ tol: Float = 1e-4) -> Bool {
    approx(a.x, b.x, tol) && approx(a.y, b.y, tol) && approx(a.z, b.z, tol)
}

// --- interiorBox: outer shell minus a 1 cm wall -------------------------------------------------
let outer = BoxFit(width: 0.42, depth: 0.62, height: 0.21,
                   center: SIMD3(1, 0.805, 2), axis: SIMD3(1, 0, 0))
let inner = interiorBox(outer, wall: 0.01)
assert(approx(inner.width, 0.40), "interior width \(inner.width)")
assert(approx(inner.depth, 0.60), "interior depth \(inner.depth)")
assert(approx(inner.height, 0.20), "interior height \(inner.height)")
assert(approx(inner.center, SIMD3(1, 0.81, 2)), "interior centre \(inner.center)")
// The interior floor is exactly one wall above the table the outer box sits on.
let outerFloor = outer.center.y - outer.height / 2
assert(approx(inner.center.y - inner.height / 2, outerFloor + 0.01), "interior floor")

// --- identity axis: world = origin + position + size/2 -------------------------------------------
let flat = BoxFit(width: 0.4, depth: 0.6, height: 0.2, center: SIMD3(1, 0.8, 2), axis: SIMD3(1, 0, 0))
let a0 = PlanAnchor(interior: flat, planeY: 0.7)
assert(approx(a0.origin, SIMD3(0.8, 0.7, 1.7)), "origin \(a0.origin)")
assert(approx(a0.perp, SIMD3(0, 0, 1)), "perp \(a0.perp)")
let pos = SIMD3<Float>(0.05, 0, 0.1), size = SIMD3<Float>(0.1, 0.2, 0.3)
assert(approx(a0.worldCenter(position: pos, size: size), a0.origin + pos + size / 2),
       "identity \(a0.worldCenter(position: pos, size: size))")

// --- axis turned 90°: bag X -> world +Z, bag Z -> world -X ---------------------------------------
let turned = BoxFit(width: 0.4, depth: 0.6, height: 0.2, center: SIMD3(1, 0.8, 2), axis: SIMD3(0, 0, 1))
let a90 = PlanAnchor(interior: turned, planeY: 0.7)
assert(approx(a90.perp, SIMD3(-1, 0, 0)), "perp \(a90.perp)")
assert(approx(a90.origin, SIMD3(1.3, 0.7, 1.8)), "origin \(a90.origin)")
assert(approx(a90.worldCenter(position: pos, size: size), SIMD3(1.05, 0.8, 1.9)),
       "turned \(a90.worldCenter(position: pos, size: size))")
// One metre along each bag axis, measured as a difference so the origin cancels.
let base = a90.worldCenter(position: SIMD3(0, 0, 0), size: SIMD3(0, 0, 0))
assert(approx(a90.worldCenter(position: SIMD3(1, 0, 0), size: SIMD3(0, 0, 0)) - base, SIMD3(0, 0, 1)), "bag X")
assert(approx(a90.worldCenter(position: SIMD3(0, 1, 0), size: SIMD3(0, 0, 0)) - base, SIMD3(0, 1, 0)), "bag Y")
assert(approx(a90.worldCenter(position: SIMD3(0, 0, 1), size: SIMD3(0, 0, 0)) - base, SIMD3(-1, 0, 0)), "bag Z")
// Right-handed in both: cross(bag X, bag Y) == bag Z.
for anchor in [a0, a90] {
    assert(approx(simd_cross(anchor.axis, SIMD3(0, 1, 0)), anchor.perp), "left-handed \(anchor.axis)")
}

// --- the reference plan, in an arbitrarily placed bag --------------------------------------------
// Parsed with JSONSerialization on purpose: this file must not need the PackingPlan module.
let planPath = "packing-core/Sources/PackingPlan/Resources/plan.json"
let raw = try! Data(contentsOf: URL(fileURLWithPath: planPath))
let doc = try! JSONSerialization.jsonObject(with: raw) as! [String: Any]
func vec(_ any: Any?) -> SIMD3<Float> {
    let d = any as! [String: Any]
    return SIMD3((d["x"] as! NSNumber).floatValue, (d["y"] as! NSNumber).floatValue, (d["z"] as! NSNumber).floatValue)
}
let dims = vec((doc["container"] as! [String: Any])["dimensions"])
let placements = doc["placements"] as! [[String: Any]]
assert(placements.count == 6, "plan.json placements \(placements.count)")

let ang: Float = 0.63  // arbitrary bag heading
let bag = BoxFit(width: dims.x, depth: dims.z, height: dims.y,
                 center: SIMD3(1.3, 0.91 + dims.y / 2, -0.4), axis: SIMD3(cos(ang), 0, sin(ang)))
let anchor = PlanAnchor(interior: bag, planeY: 0.91)
for p in placements {
    let pos = vec(p["position"]), size = vec(p["size"])
    let centre = anchor.worldCenter(position: pos, size: size)
    for sx: Float in [-0.5, 0.5] { for sy: Float in [-0.5, 0.5] { for sz: Float in [-0.5, 0.5] {
        let corner = centre + anchor.axis * (sx * size.x) + SIMD3(0, sy * size.y, 0) + anchor.perp * (sz * size.z)
        // Independent containment check: measure the corner against the bag box's own half-extents.
        let d = corner - bag.center
        let e: Float = 1e-4
        assert(abs(simd_dot(d, anchor.axis)) <= bag.width / 2 + e, "step \(p["step"]!) out on X")
        assert(abs(d.y) <= bag.height / 2 + e, "step \(p["step"]!) out on Y")
        assert(abs(simd_dot(d, anchor.perp)) <= bag.depth / 2 + e, "step \(p["step"]!) out on Z")
    } } }
}

print("plan anchor: \(placements.count) placements inside the bag, axes right-handed — ok")
