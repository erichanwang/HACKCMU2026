// LiDAR hull footprints (Spike/Geometry.swift's `decimateHull` / `ScannedItem.footprint`), on
// the real code path, no server/device involved. Mirrors scripts/ar_sim.py's shapes (box,
// l-shape, cylinder) directly in Swift. Non-zero exit = a check failed.
//
// Frame under test (docs/LIDAR_HULLS.md): `ScannedItem.footprint` is the item's LOCAL (x, z)
// plane, METRES (this struct's team contract -- SCAN_OUTPUT.md, `dimensions`), relative to the
// box centre, width along +x, depth along +z. That's the frame these checks assert against --
// world-frame or centimetre points here would silently look "plausible" (finite floats near the
// right magnitude) while still being wrong, which is the whole risk this test suite exists to
// catch.
import Foundation
#if canImport(simd)
import simd
#endif  // Linux: tests/swift/SimdShim.swift supplies the few simd functions used here

let planeY: Float = 0.80

var failures: [String] = []
func check(_ ok: Bool, _ msg: @autoclosure () -> String) {
    if !ok { failures.append(msg()); print("FAIL: \(msg())") }
}

func rotate(_ p: SIMD2<Float>, _ angle: Float) -> SIMD2<Float> {
    let c = cos(angle), s = sin(angle)
    return SIMD2(p.x * c - p.y * s, p.x * s + p.y * c)
}

/// World points for a local (x, z) footprint sampled at height `h` above `planeY`, placed at
/// world centre `center` and rotated by `angle` -- world = rotate(local) + center, y = planeY + h.
func worldPoints(_ localXZ: [SIMD2<Float>], h: Float, center: SIMD2<Float>, angle: Float) -> [SIMD3<Float>] {
    localXZ.map { p in
        let w = rotate(p, angle) + center
        return SIMD3(w.x, planeY + h, w.y)
    }
}

func grid(_ lo: SIMD2<Float>, _ hi: SIMD2<Float>, _ n: Int) -> [SIMD2<Float>] {
    var pts: [SIMD2<Float>] = []
    for i in 0..<n {
        for j in 0..<n {
            let u = Float(i) / Float(n - 1), v = Float(j) / Float(n - 1)
            pts.append(SIMD2(lo.x + (hi.x - lo.x) * u, lo.y + (hi.y - lo.y) * v))
        }
    }
    return pts
}

/// Point-in-convex-polygon (CCW), with `eps` slack so a point exactly on the boundary passes.
func inside(_ p: SIMD2<Float>, _ poly: [SIMD2<Float>], eps: Float = 1e-4) -> Bool {
    for i in 0..<poly.count {
        let a = poly[i], b = poly[(i + 1) % poly.count]
        let cross = (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)
        if cross < -eps { return false }
    }
    return true
}

func local2(_ p: [Float]) -> SIMD2<Float> { SIMD2(p[0], p[1]) }

// --- 1. rotated rectangle: hull should be ~4 corners, tight ------------------------------

do {
    let w: Float = 0.20, d: Float = 0.12, h: Float = 0.05, angle: Float = 0.4
    let center = SIMD2<Float>(1.0, 2.0)
    let local = grid(SIMD2(-w / 2, -d / 2), SIMD2(w / 2, d / 2), 5)
    let pts = worldPoints(local, h: h, center: center, angle: angle)
    guard let box = fitBox(points: pts, planeY: planeY, padding: 0) else {
        check(false, "rotated rectangle: fitBox failed"); fatalError()
    }
    // A perfect rectangle's convex hull is exactly its 4 corners; a few extra near-collinear
    // edge points from float rounding of the rotation are expected and harmless (same as real
    // LiDAR jitter) -- "a handful", not exactly 4.
    check(box.hull.count <= 10, "rotated rectangle: raw hull should be a handful of corners, got \(box.hull.count)")
    guard let fp = ScannedItem.footprint(from: box) else { check(false, "rotated rectangle: no footprint"); fatalError() }
    check(fp.count >= 4 && fp.count <= 10, "rotated rectangle: footprint should be ~4 corners, got \(fp.count)")
    // Tight: every vertex should sit near a true edge (within jitter-free float slack). Use the
    // box's OWN width/depth, not the literal w/d above -- minAreaRect can label either physical
    // edge "width" first (ar_sim.py's dims_m has the same caveat), so hx/hz must come from the
    // same frame the footprint itself was built in, not from which edge we happened to name w.
    let hx = box.width / 2, hz = box.depth / 2
    for p in fp {
        let x = p[0], z = p[1]
        check(abs(abs(x) - hx) < 1e-3 || abs(abs(z) - hz) < 1e-3,
              "rotated rectangle: footprint vertex (\(x), \(z)) is not near a box edge (hx=\(hx), hz=\(hz))")
    }
    print("1. rotated rectangle: raw hull \(box.hull.count) pts -> footprint \(fp.count) pts, all near true corners -- ok")
}

// --- 2. L-shape: the hull must NOT fill the notch -----------------------------------------

do {
    let w: Float = 0.14, d: Float = 0.12, h: Float = 0.08, angle: Float = 0.3
    let center = SIMD2<Float>(-1.5, 0.6)
    // Full grid minus the lx > 0 && lz > 0 quadrant -- same construction as
    // scripts/ar_sim.py's l_shape_points.
    let local = grid(SIMD2(-w / 2, -d / 2), SIMD2(w / 2, d / 2), 20).filter { !($0.x > 0 && $0.y > 0) }
    let pts = worldPoints(local, h: h, center: center, angle: angle)
    guard let box = fitBox(points: pts, planeY: planeY, padding: 0) else {
        check(false, "l-shape: fitBox failed"); fatalError()
    }
    guard let fp = ScannedItem.footprint(from: box) else { check(false, "l-shape: no footprint"); fatalError() }
    let poly = fp.map { local2($0) }
    // A point deep in the missing quadrant (near its far corner) must be OUTSIDE the footprint --
    // that's the entire reason a hull beats a bounding box here. Note the box's own axes may not
    // align with (w, h) local x/z (minAreaRect can pick either edge as "width" first), so probe
    // near all four quadrant corners at 90% depth and require at least one clearly outside.
    let probes = [SIMD2<Float>(0.9, 0.9), SIMD2(-0.9, 0.9), SIMD2(0.9, -0.9), SIMD2(-0.9, -0.9)]
        .map { SIMD2($0.x * w / 2, $0.y * d / 2) }
    let outsideCount = probes.filter { !inside($0, poly, eps: 0) }.count
    check(outsideCount >= 1, "l-shape: footprint fills the missing quadrant -- no probe corner is excluded")
    // And it must still be a real polygon, not degenerate.
    check(fp.count >= 4, "l-shape: footprint has too few vertices to represent a notch (\(fp.count))")
    print("2. l-shape: footprint has \(fp.count) vertices, \(outsideCount)/4 quadrant probes correctly excluded -- ok")
}

// --- 3. circle: hull approximates it within decimation tolerance --------------------------

do {
    let radius: Float = 0.06, h: Float = 0.20, angle: Float = 0.0
    let center = SIMD2<Float>(0.0, 0.0)
    var local: [SIMD2<Float>] = []
    for i in 0..<48 {
        let t = Float(i) / 48 * 2 * .pi
        local.append(SIMD2(radius * cos(t), radius * sin(t)))
    }
    let pts = worldPoints(local, h: h, center: center, angle: angle)
    guard let box = fitBox(points: pts, planeY: planeY, padding: 0) else {
        check(false, "circle: fitBox failed"); fatalError()
    }
    guard let fp = ScannedItem.footprint(from: box) else { check(false, "circle: no footprint"); fatalError() }
    check(fp.count <= maxFootprintVertices, "circle: footprint should be decimated to <= \(maxFootprintVertices), got \(fp.count)")
    let radii = fp.map { simd_length(local2($0)) }
    let minR = radii.min()!, maxR = radii.max()!
    // A 16-ish-gon decimated (then conservatively expanded) from a 48-point circle sample should
    // stay within ~15% of the true radius on both sides -- tight enough to prove it approximates
    // a circle, loose enough to not be a tautology about the exact scale-out amount.
    check(minR > radius * 0.85, "circle: footprint dips to \(minR) m, below 85% of radius \(radius) m")
    check(maxR < radius * 1.15, "circle: footprint bulges to \(maxR) m, above 115% of radius \(radius) m")
    print("3. circle: radius \(radius) m -> footprint radii \(minR)-\(maxR) m over \(fp.count) vertices -- ok")
}

// --- 4. degenerate cluster: no crash, no hull rather than a bad one ------------------------

do {
    let degenerate = BoxFit(width: 0.1, depth: 0.1, height: 0.1,
                             center: SIMD3(0, 0.05, 0), axis: SIMD3(1, 0, 0), hull: [])
    check(ScannedItem.footprint(from: degenerate) == nil, "degenerate hull (0 points) should yield no footprint")
    let two = BoxFit(width: 0.1, depth: 0.1, height: 0.1,
                      center: SIMD3(0, 0.05, 0), axis: SIMD3(1, 0, 0), hull: [SIMD2(0, 0), SIMD2(1, 1)])
    check(ScannedItem.footprint(from: two) == nil, "degenerate hull (2 points) should yield no footprint")
    // Also exercise the real path: a scan with too few / collinear points never reaches a hull.
    let linePts = (0..<10).map { SIMD3<Float>(Float($0) * 0.01, planeY, 0) }
    check(fitBox(points: linePts, planeY: planeY, padding: 0) == nil, "collinear points: fitBox should be nil")
    print("4. degenerate clusters: no footprint, no crash -- ok")
}

// --- 5. consistency: the footprint must fit inside the box the same scan produced ----------

do {
    // Re-run the L-shape (the case most likely to overshoot, since its raw hull runs to many
    // vertices and decimateHull's conservative expansion is the only thing keeping it in bounds)
    // through the same 1e-6-slack containment rule physics/geometry.py's `footprint_local`
    // enforces server-side, so a mismatch is caught here rather than silently ignored.
    let w: Float = 0.14, d: Float = 0.12, h: Float = 0.08, angle: Float = 0.3
    let center = SIMD2<Float>(-1.5, 0.6)
    let local = grid(SIMD2(-w / 2, -d / 2), SIMD2(w / 2, d / 2), 20).filter { !($0.x > 0 && $0.y > 0) }
    let pts = worldPoints(local, h: h, center: center, angle: angle)
    let box = fitBox(points: pts, planeY: planeY, padding: 0)!
    let fp = ScannedItem.footprint(from: box)!
    let hx = box.width / 2, hz = box.depth / 2
    for p in fp {
        check(abs(p[0]) <= hx + 1e-6, "l-shape footprint x=\(p[0]) exceeds half-width \(hx)")
        check(abs(p[1]) <= hz + 1e-6, "l-shape footprint z=\(p[1]) exceeds half-depth \(hz)")
    }
    print("5. consistency: every l-shape footprint vertex is within its own box's half-dimensions -- ok")
}

// --- 6. decimateHull itself: conservative (superset of the original hull) -----------------

do {
    // A 40-point near-circle: more vertices than maxFootprintVertices, so this actually
    // exercises the drop-and-expand path, not just the pass-through for a small hull.
    var big: [SIMD2<Float>] = []
    for i in 0..<40 {
        let t = Float(i) / 40 * 2 * .pi
        big.append(SIMD2(cos(t) * (1 + 0.05 * sin(5 * t)), sin(t) * (1 + 0.05 * sin(5 * t))))
    }
    let decimated = decimateHull(big)
    check(decimated.count <= maxFootprintVertices, "decimateHull: did not reduce to <= \(maxFootprintVertices) (\(decimated.count))")
    for p in big {
        check(inside(p, decimated, eps: 1e-3), "decimateHull: original hull point \(p) fell outside the decimated polygon")
    }
    print("6. decimateHull: \(big.count) -> \(decimated.count) vertices, every original point still contained -- ok")
}

print("")
if failures.isEmpty {
    print("hull footprint checks: all ok")
} else {
    print("\(failures.count) check(s) FAILED")
    exit(1)
}
