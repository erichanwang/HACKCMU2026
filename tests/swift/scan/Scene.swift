// Synthetic ARKit scenes for the scan simulation: fused-mesh triangles above a table plane,
// densified and jittered the way `ScanView.tap` feeds the real geometry functions.
import Foundation
#if canImport(simd)
import simd
#endif

// Tuning constants, copied from Spike/ScanView.swift — that file imports ARKit, so it cannot
// be compiled here. Keep in sync if they are retuned.
let paddingMeters: Float = 0.005
let minHeightMeters: Float = 0.01
let clusterCellMeters: Float = 0.02
let shapeCellMeters: Float = 0.01

typealias Tri = (SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)

/// Deterministic jitter, so a failing run reproduces.
struct RNG {
    var s: UInt64
    mutating func unit() -> Float {  // [-1, 1)
        s = s &* 6364136223846793005 &+ 1442695040888963407
        return Float(s >> 40) / Float(1 << 23) - 1
    }
    mutating func jitter(_ amp: Float) -> SIMD3<Float> {
        amp == 0 ? .zero : SIMD3(unit() * amp, unit() * amp, unit() * amp)
    }
}

/// The quad `o, o+u, o+u+v, o+v` cut into ~`step`-sized triangles — the size ARKit's
/// scene reconstruction actually hands over.
func quadTris(_ o: SIMD3<Float>, _ u: SIMD3<Float>, _ v: SIMD3<Float>, step: Float = 0.03) -> [Tri] {
    let nu = max(1, Int((simd_length(u) / step).rounded(.up)))
    let nv = max(1, Int((simd_length(v) / step).rounded(.up)))
    func p(_ a: Int, _ b: Int) -> SIMD3<Float> { o + u * (Float(a) / Float(nu)) + v * (Float(b) / Float(nv)) }
    var out: [Tri] = []
    for i in 0..<nu { for j in 0..<nv {
        out.append((p(i, j), p(i + 1, j), p(i, j + 1)))
        out.append((p(i + 1, j), p(i + 1, j + 1), p(i, j + 1)))
    } }
    return out
}

/// Top face plus four walls of a `w x d x h` box standing on `planeY`, centred at `c`, yawed `deg`.
func boxTris(_ w: Float, _ d: Float, _ h: Float, at c: SIMD2<Float>, yaw deg: Float, planeY: Float) -> [Tri] {
    let t = deg * .pi / 180
    let ax = SIMD3<Float>(cos(t), 0, sin(t)), pz = SIMD3<Float>(-sin(t), 0, cos(t))
    let o = SIMD3<Float>(c.x, planeY, c.y) - ax * (w / 2) - pz * (d / 2)  // bottom corner
    let u = ax * w, v = pz * d, up = SIMD3<Float>(0, h, 0)
    return quadTris(o + up, u, v)
        + quadTris(o, u, up) + quadTris(o + v, u, up)
        + quadTris(o, v, up) + quadTris(o + u, v, up)
}

/// What `ScanView.tap` does to the mesh: jitter it like LiDAR, drop triangles at table level,
/// sample each one densely, drop the samples that are still table level.
func scan(_ tris: [Tri], planeY: Float, noise: Float = 0, seed: UInt64 = 0x5EED) -> [SIMD3<Float>] {
    var rng = RNG(s: seed)
    var out: [SIMD3<Float>] = []
    for t in tris {
        let a = t.0 + rng.jitter(noise), b = t.1 + rng.jitter(noise), c = t.2 + rng.jitter(noise)
        guard max(a.y, b.y, c.y) - planeY > minHeightMeters else { continue }
        out += densify(a, b, c, spacing: shapeCellMeters / 2).filter { $0.y - planeY > minHeightMeters }
    }
    return out
}

// --- checks and the results table -------------------------------------------------------

var failures: [String] = []
func check(_ ok: Bool, _ msg: @autoclosure () -> String) {
    if !ok { failures.append(msg()); print("FAIL: \(msg())") }
}

struct Row { var name: String, truth: String, got: String, err: String }
var table: [Row] = []
func record(_ name: String, _ truth: String, _ got: String, _ err: String) {
    table.append(Row(name: name, truth: truth, got: got, err: err))
}

func cm(_ x: Float) -> String { String(format: "%.1f", x * 100) }
func mm(_ x: Float) -> String { String(format: "%+.1f", x * 1000) }

func printTable() {
    let all = [Row(name: "scenario", truth: "truth", got: "measured", err: "error")] + table
    let w = (0..<4).map { k in all.map { [$0.name, $0.truth, $0.got, $0.err][k].count }.max()! }
    func pad(_ s: String, _ n: Int) -> String { s + String(repeating: " ", count: n - s.count) }
    for (k, r) in all.enumerated() {
        print("\(pad(r.name, w[0]))  \(pad(r.truth, w[1]))  \(pad(r.got, w[2]))  \(r.err)")
        if k == 0 { print(String(repeating: "-", count: w.reduce(6, +))) }
    }
}
