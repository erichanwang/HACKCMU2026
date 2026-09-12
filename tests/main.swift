import simd

func approx(_ a: Float, _ b: Float, _ tol: Float = 1e-3) -> Bool { abs(a - b) < tol }

// A 0.10 x 0.20 x 0.05 box rotated 30° on a table at y = 0.7, sampled on its faces.
let ang: Float = .pi / 6
let rot = simd_float2x2(SIMD2(cos(ang), sin(ang)), SIMD2(-sin(ang), cos(ang)))
var pts: [SIMD3<Float>] = []
for i in 0...20 { for j in 0...40 {
    let local = SIMD2<Float>(Float(i) / 20 * 0.10 - 0.05, Float(j) / 40 * 0.20 - 0.10)
    let w = rot * local + SIMD2<Float>(1.0, 2.0)
    pts.append(SIMD3(w.x, 0.7 + 0.05, w.y))  // top face
    if i == 0 || i == 20 || j == 0 || j == 40 { pts.append(SIMD3(w.x, 0.7 + 0.02, w.y)) } // side walls
} }
let fit = fitBox(points: pts, planeY: 0.7, padding: 0)!
let dims = [fit.width, fit.depth].sorted()
assert(approx(dims[0], 0.10) && approx(dims[1], 0.20), "footprint \(dims)")
assert(approx(fit.height, 0.05), "height \(fit.height)")
assert(approx(fit.center.x, 1.0) && approx(fit.center.z, 2.0), "center \(fit.center)")

// A second object 10 cm away must not be swallowed by the cluster.
let other = (0...10).map { SIMD3<Float>(1.5 + Float($0) * 0.005, 0.75, 2.0) }
let cluster = connectedCluster(pts + other, seed: SIMD3(1.0, 0.75, 2.0), cell: 0.02)
assert(cluster.count == pts.count, "cluster \(cluster.count) vs \(pts.count)")

print("geometry ok: \(dims.map { $0 * 100 }) cm footprint, \(fit.height * 100) cm tall")
