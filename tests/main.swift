import Foundation
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

// Heightmap of the full box: every cell ~5 cm.
let hm = heightMap(points: pts, box: fit, planeY: 0.7, cell: 0.01)
assert(hm.count == 10 || hm.count == 20, "grid \(hm.count)x\(hm[0].count)")
assert(hm.allSatisfy { $0.allSatisfy { approx($0, 0.05, 0.005) } }, "box heightmap not flat")

// An L-shape: drop one quadrant of the top face. Its cells must read 0, the rest ~5 cm.
let lPts = pts.filter { p in
    let d = p - SIMD3<Float>(1.0, 0.75, 2.0)
    return !(simd_dot(d, fit.axis) > 0 && simd_dot(d, SIMD3<Float>(-fit.axis.z, 0, fit.axis.x)) > 0)
}
let lFit = fitBox(points: lPts, planeY: 0.7, padding: 0)!
let lhm = heightMap(points: lPts, box: lFit, planeY: 0.7, cell: 0.01)
let flat = lhm.flatMap { $0 }
let empty = flat.filter { $0 == 0 }.count
assert(empty > flat.count / 5 && empty < flat.count / 3, "L-shape hole \(empty)/\(flat.count)")
assert(flat.filter { $0 > 0 }.allSatisfy { approx($0, 0.05, 0.005) }, "L-shape heights")

// Densify adds interior samples at the requested spacing.
let tri = densify(SIMD3(0, 0, 0), SIMD3(0.1, 0, 0), SIMD3(0, 0, 0.1), spacing: 0.01)
assert(tri.count == 66, "densify \(tri.count)")

print(ScannedItem(lFit, heights: lhm, cell: 0.01, suitcaseId: "s1").asciiMap)
let encoded = String(data: try! JSONEncoder().encode(ScannedItem(fit, heights: hm, cell: 0.01, suitcaseId: "s1")), encoding: .utf8)!
assert(encoded.contains("\"dimensions\"") && !encoded.contains("\"label\""), encoded)
print("geometry ok: \(dims.map { $0 * 100 }) cm footprint, \(fit.height * 100) cm tall")
