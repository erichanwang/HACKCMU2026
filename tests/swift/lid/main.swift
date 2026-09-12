// Open-lid suitcase scans, on the real Spike/Geometry.swift (fitBox + rimHeight). Mirrors
// scripts/ar_sim.py's own `box_points(open_top: true)` / `lid_points` model (a rim + walls
// cavity, plus a slab hinged off the back-top edge, leaning past vertical into frame) directly
// in Swift so this exercises the same code path as ScanView's tap without a server round trip.
//
// Bag-local frame before the world yaw below: x = width, z = depth, y = height, floor at y = 0,
// hinge at the back-top edge (z = +d/2, y = h). Non-zero exit = a check failed.
import Foundation
#if canImport(simd)
import simd
#endif  // Linux: tests/swift/SimdShim.swift supplies the few simd functions used here

let w: Float = 0.55, d: Float = 0.35, h: Float = 0.40   // Spike/ScanView.swift-scale carry-on
let planeY: Float = 0.80
let yaw: Float = 20                                      // scanned at an arbitrary angle, like a real tap
let center = SIMD2<Float>(1.0, 2.0)
let noise: Float = 0.0015                                 // LiDAR jitter, scripts/ar_sim.py's own NOISE

/// Deterministic jitter, so a failing run reproduces.
struct RNG {
    var s: UInt64
    mutating func unit() -> Float {
        s = s &* 6364136223846793005 &+ 1442695040888963407
        return Float(s >> 40) / Float(1 << 23) - 1
    }
    mutating func jitter(_ amp: Float) -> SIMD3<Float> {
        amp == 0 ? .zero : SIMD3(unit() * amp, unit() * amp, unit() * amp)
    }
}

func toWorld(_ p: SIMD3<Float>) -> SIMD3<Float> {
    let t = yaw * .pi / 180, c = cos(t), s = sin(t)
    return SIMD3(center.x + p.x * c - p.z * s, planeY + p.y, center.y + p.x * s + p.z * c)
}

/// The base cavity: rim perimeter at height h, plus four walls 0...h at the extreme x/z --
/// scripts/ar_sim.py's `box_points(..., open_top: true)`. No floor is visible from outside.
func cavityPoints(nTop: Int = 14, nSide: Int = 6, rng: inout RNG) -> [SIMD3<Float>] {
    var local: [SIMD3<Float>] = []
    for i in 0..<nTop {
        let t = -w / 2 + w * Float(i) / Float(nTop - 1)
        local.append(SIMD3(t, h, -d / 2)); local.append(SIMD3(t, h, d / 2))
    }
    for i in 0..<nTop {
        let t = -d / 2 + d * Float(i) / Float(nTop - 1)
        local.append(SIMD3(-w / 2, h, t)); local.append(SIMD3(w / 2, h, t))
    }
    for i in 0..<nSide {
        let t = -w / 2 + w * Float(i) / Float(nSide - 1)
        for j in 0..<nSide {
            let ly = h * Float(j) / Float(nSide - 1)
            local.append(SIMD3(t, ly, -d / 2)); local.append(SIMD3(t, ly, d / 2))
        }
    }
    for i in 0..<nSide {
        let t = -d / 2 + d * Float(i) / Float(nSide - 1)
        for j in 0..<nSide {
            let ly = h * Float(j) / Float(nSide - 1)
            local.append(SIMD3(-w / 2, ly, t)); local.append(SIMD3(w / 2, ly, t))
        }
    }
    return local.map { toWorld($0 + rng.jitter(noise)) }
}

/// The open lid: a slab hinged at the back-top edge (z = +d/2, y = h), tipped `openDeg` from
/// closed (0 deg, flush with the rim) through vertical (90 deg) to leaning back past it (> 90
/// deg) -- scripts/ar_sim.py's own `lid_points`, parameterised the same way (its `LID_LEAN_DEG`
/// is `openDeg - 90` here). `mirror` flips which way the panel tips, for a hinge that opens
/// toward the camera instead of away from it -- rimHeight only ever looks at height, so this
/// should make no difference, and the "toward camera" scenario below checks that it doesn't.
func lidPoints(openDeg: Float, extent: Float, mirror: Bool, n: Int = 10, rng: inout RNG) -> [SIMD3<Float>] {
    let phi = (openDeg - 90) * Float.pi / 180
    let sign: Float = mirror ? -1 : 1
    var local: [SIMD3<Float>] = []
    for i in 0..<n {
        let s = extent * Float(i) / Float(n - 1)
        let lz = sign * (d / 2 + s * sin(phi)), ly = h + s * cos(phi)
        for j in 0..<n {
            let lx = -w / 2 + w * Float(j) / Float(n - 1)
            local.append(SIMD3(lx, ly, lz))
        }
    }
    return local.map { toWorld($0 + rng.jitter(noise)) }
}

struct Scenario {
    var name: String
    var openDeg: Float?     // nil = closed, no lid at all
    var extent: Float
    var mirror: Bool
    var tolerance: Float    // max allowed |degraded - true| per dimension, metres
}

let scenarios: [Scenario] = [
    Scenario(name: "closed bag (no lid)", openDeg: nil, extent: 0, mirror: false, tolerance: 1e-4),
    Scenario(name: "lid open 90°",  openDeg: 90,  extent: 0.08, mirror: false, tolerance: 0.015),
    Scenario(name: "lid open 120°", openDeg: 120, extent: 0.08, mirror: false, tolerance: 0.015),
    Scenario(name: "lid open 45°",  openDeg: 45,  extent: 0.08, mirror: false, tolerance: 0.015),
    Scenario(name: "lid open 90°, toward camera",  openDeg: 90, extent: 0.08, mirror: true,  tolerance: 0.015),
    Scenario(name: "lid open 90°, away from camera", openDeg: 90, extent: 0.08, mirror: false, tolerance: 0.015),
    // A soft bag's flap doesn't stand rigid, but it still clears the rim -- a shallower angle
    // and a shorter reach than the hard-shell cases above, not a taller/further one.
    Scenario(name: "soft bag, lid flops (105°, limp)", openDeg: 105, extent: 0.05, mirror: false, tolerance: 0.015),
]

var failures: [String] = []
func check(_ ok: Bool, _ msg: @autoclosure () -> String) {
    if !ok { failures.append(msg()); print("FAIL: \(msg())") }
}
func cm(_ x: Float) -> String { String(format: "%.1f", x * 100) }
func mm(_ x: Float) -> String { String(format: "%+.1f", x * 1000) }

print("scenario                                  true (m)             fitted (m)           max |delta|")
print(String(repeating: "-", count: 95))
for sc in scenarios {
    var rng = RNG(s: 0x5EED)
    let base = cavityPoints(rng: &rng)
    let trueBox = fitBox(points: base, planeY: planeY, padding: 0)!
    let all: [SIMD3<Float>]
    if let openDeg = sc.openDeg {
        all = base + lidPoints(openDeg: openDeg, extent: sc.extent, mirror: sc.mirror, rng: &rng)
    } else {
        all = base
    }
    let gotBox = fitBox(points: all, planeY: planeY, padding: 0, trimAboveRim: true)!
    let trueDims = [trueBox.width, trueBox.depth].sorted() + [trueBox.height]
    let gotDims = [gotBox.width, gotBox.depth].sorted() + [gotBox.height]
    let errs = zip(gotDims, trueDims).map { $0 - $1 }
    print("\(sc.name.padding(toLength: 42, withPad: " ", startingAt: 0)) "
          + "\(cm(trueDims[0]))x\(cm(trueDims[1]))x\(cm(trueDims[2]))       "
          + "\(cm(gotDims[0]))x\(cm(gotDims[1]))x\(cm(gotDims[2]))       "
          + errs.map { mm($0) }.joined(separator: "/") + " mm")
    for (i, e) in errs.enumerated() {
        check(abs(e) <= sc.tolerance,
              "\(sc.name): dim \(i) off by \(mm(e)) mm (want <= \(mm(sc.tolerance)) mm)")
    }
}

print("")
if failures.isEmpty {
    // An ITEM has no lid, and must never be trimmed: rimHeight picks the densest height band,
    // so an item whose top face was not densely captured (occlusion, far side never seen) has
    // no spike at its top, every band ties, and the lowest wins -- a 22 cm bottle fitted as a
    // 1.5 cm disc. This is why trimAboveRim is opt-in and ScanView passes it only in .suitcase.
    var wallOnly: [SIMD3<Float>] = []
    for i in 0..<48 {
        let t = Float(i) / 48 * 2 * .pi
        for k in 0...120 {
            wallOnly.append(SIMD3(0.04 * cos(t), planeY + Float(k) / 120 * 0.22, 0.04 * sin(t)))
        }
    }
    let itemBox = fitBox(points: wallOnly, planeY: planeY, padding: 0)!
    let trimmedBox = fitBox(points: wallOnly, planeY: planeY, padding: 0, trimAboveRim: true)!
    print(String(format: "%-42@ true 0.220 m  item %.3f m  trimmed %.3f m",
                 "occluded-top item (never trim an item)", itemBox.height, trimmedBox.height))
    guard abs(itemBox.height - 0.22) < 0.015 else {
        print("FAIL: an item scan was truncated to \(itemBox.height) m"); exit(1)
    }
    guard trimmedBox.height < 0.05 else {
        print("FAIL: trimAboveRim no longer truncates, so this regression is no longer pinned"); exit(1)
    }

    print("lid segmentation: fitBox recovers the closed-lid interior within tolerance on every scenario -- ok")
} else {
    print("\(failures.count) check(s) FAILED")
    exit(1)
}
