import Foundation
#if canImport(simd)
import simd
#endif

// How far a drawn placement lands from where it truly is, when every scan input is a little wrong.
// Ground truth: one bag, one 7-box plan that fills it exactly. Perturb planeY / axis / dimensions /
// world-origin drift, push the same plan through PlanAnchor with the perturbed bag, and measure the
// world-space gap against the unperturbed placement. Deterministic: one seeded RNG, printed seed.

// --- seeded RNG (stdlib has no seedable generator) ------------------------------------------------
struct SplitMix64: RandomNumberGenerator {
    var state: UInt64
    init(seed: UInt64) { state = seed }
    mutating func next() -> UInt64 {
        state &+= 0x9E3779B97F4A7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
        return z ^ (z >> 31)
    }
}
let seed: UInt64 = 20260912
var rng = SplitMix64(seed: seed)

func deg2rad(_ d: Float) -> Float { d * .pi / 180 }

/// Rotate a horizontal (y=0) unit vector by `theta` radians about world up.
func rotateY(_ v: SIMD3<Float>, _ theta: Float) -> SIMD3<Float> {
    let c = cos(theta), s = sin(theta)
    return SIMD3(v.x * c - v.z * s, 0, v.x * s + v.z * c)
}

// --- ground truth: one hardshell bag, wall 2 cm --------------------------------------------------
let wall: Float = 0.02
let tableY: Float = 0.90
let trueAxisAngle: Float = 0.4  // ~23 degrees, so no axis is accidentally world-aligned
let trueOuter = BoxFit(width: 0.46, depth: 0.66, height: 0.28,
                        center: SIMD3(0.5, tableY + 0.14, 1.2),
                        axis: SIMD3(cos(trueAxisAngle), 0, sin(trueAxisAngle)))
let trueInterior = interiorBox(trueOuter, wall: wall)
let truePlaneY = tableY + wall  // per the D2 contract: planeY = table + wall
let trueAnchor = PlanAnchor(interior: trueInterior, planeY: truePlaneY)
// interior is 0.42 x 0.62 x 0.26 (width x depth x height) — sanity-checked by the assert below.
assert(abs(trueInterior.width - 0.42) < 1e-4 && abs(trueInterior.depth - 0.62) < 1e-4
       && abs(trueInterior.height - 0.26) < 1e-4, "fixture interior drifted, update the plan below")

// --- a plausible full plan: two layers, 7 boxes, exactly filling the interior ---------------------
struct Placement { let step: Int; let pos: SIMD3<Float>; let size: SIMD3<Float> }
let plan: [Placement] = [
    Placement(step: 1, pos: SIMD3(0.00, 0.00, 0.00), size: SIMD3(0.14, 0.13, 0.62)),
    Placement(step: 2, pos: SIMD3(0.14, 0.00, 0.00), size: SIMD3(0.14, 0.13, 0.62)),
    Placement(step: 3, pos: SIMD3(0.28, 0.00, 0.00), size: SIMD3(0.14, 0.13, 0.62)),
    Placement(step: 4, pos: SIMD3(0.00, 0.13, 0.00), size: SIMD3(0.21, 0.13, 0.31)),
    Placement(step: 5, pos: SIMD3(0.21, 0.13, 0.00), size: SIMD3(0.21, 0.13, 0.31)),
    Placement(step: 6, pos: SIMD3(0.00, 0.13, 0.31), size: SIMD3(0.21, 0.13, 0.31)),
    Placement(step: 7, pos: SIMD3(0.21, 0.13, 0.31), size: SIMD3(0.21, 0.13, 0.31)),
]

/// The 8 world-space corners of one placement, given an anchor.
func corners(_ anchor: PlanAnchor, _ p: Placement) -> [SIMD3<Float>] {
    let c = anchor.worldCenter(position: p.pos, size: p.size)
    var out: [SIMD3<Float>] = []
    for sx: Float in [-0.5, 0.5] { for sy: Float in [-0.5, 0.5] { for sz: Float in [-0.5, 0.5] {
        out.append(c + anchor.axis * (sx * p.size.x) + SIMD3(0, sy * p.size.y, 0) + anchor.perp * (sz * p.size.z))
    } } }
    return out
}

// The reference plan fills the bag edge-to-edge (0 slack, as a real solver plan does), so *strict*
// corner-in-bag containment is violated by any perturbation at all and is useless as a metric — it
// would read 0% even at a sub-millimetre error. What actually matters on camera is the size of the
// gap, per the task's own framing: ~3mm is invisible, ~3cm reads as broken. Call a placement
// "believable" if its worst corner is under this bound.
let believableThreshold: Float = 0.015  // 1.5cm: roughly half the 3cm "looks broken" the task names

/// True if every corner of `p`, drawn with `anchor` and shifted by `drift`, is within
/// `believableThreshold` of where it truly belongs.
func isBelievable(_ anchor: PlanAnchor, _ p: Placement, drift: SIMD3<Float>) -> Bool {
    let truth = corners(trueAnchor, p)
    let observed = corners(anchor, p).map { $0 + drift }
    for i in 0..<8 { if simd_length(observed[i] - truth[i]) > believableThreshold { return false } }
    return true
}

struct Perturbation {
    var dPlaneY: Float = 0        // metres
    var dAxisDeg: Float = 0       // degrees, about world up
    var dWidth: Float = 0         // metres, applied to outer width/depth/height alike
    var drift: SIMD3<Float> = .zero  // metres, added to every world point post-hoc
}

struct Result { let maxErr: Float; let meanErr: Float; let believableFrac: Double }

/// Run one perturbation through PlanAnchor and measure world-space error against ground truth.
func evaluate(_ pert: Perturbation) -> Result {
    let outer = BoxFit(width: trueOuter.width + pert.dWidth, depth: trueOuter.depth + pert.dWidth,
                        height: trueOuter.height + pert.dWidth,
                        center: trueOuter.center, axis: rotateY(trueOuter.axis, deg2rad(pert.dAxisDeg)))
    let interior = interiorBox(outer, wall: wall)
    let anchor = PlanAnchor(interior: interior, planeY: truePlaneY + pert.dPlaneY)

    var errs: [Float] = []
    var believable = 0
    for p in plan {
        let truth = corners(trueAnchor, p)
        let observed = corners(anchor, p).map { $0 + pert.drift }
        for i in 0..<8 { errs.append(simd_length(observed[i] - truth[i])) }
        if isBelievable(anchor, p, drift: pert.drift) { believable += 1 }
    }
    return Result(maxErr: errs.max()!, meanErr: errs.reduce(0, +) / Float(errs.count),
                  believableFrac: Double(believable) / Double(plan.count))
}

func fmt(_ x: Float) -> String { String(format: "%7.4f", x) }
func pct(_ x: Double) -> String { String(format: "%5.0f%%", x * 100) }

print("bag interior: \(trueInterior.width)m x \(trueInterior.depth)m x \(trueInterior.height)m, wall \(wall)m, seed \(seed)")
print("")

// --- Section 1: single-factor sweeps, everything else exact ---------------------------------------
print("== single-factor sweep (each input alone) ==")
print("factor         magnitude    max corner err   mean corner err   placements still believable (<1.5cm)")
struct Sweep { let name: String; let unit: String; let values: [Float]; let make: (Float) -> Perturbation }
let sweeps: [Sweep] = [
    Sweep(name: "planeY", unit: "cm", values: [0.5, 1, 2, 3]) { Perturbation(dPlaneY: $0 / 100) },
    Sweep(name: "axis", unit: "deg", values: [1, 2, 3, 5]) { Perturbation(dAxisDeg: $0) },
    Sweep(name: "dimensions", unit: "cm", values: [1]) { Perturbation(dWidth: $0 / 100) },
    Sweep(name: "origin drift", unit: "cm", values: [1, 2, 3, 5]) {
        Perturbation(drift: SIMD3(1, 0, 1) / Float(2).squareRoot() * ($0 / 100))
    },
]
for sweep in sweeps {
    for v in sweep.values {
        let r = evaluate(sweep.make(v))
        print("\(sweep.name.padding(toLength: 14, withPad: " ", startingAt: 0)) "
            + "\(String(format: "%5.1f", v))\(sweep.unit)      \(fmt(r.maxErr))m        \(fmt(r.meanErr))m         \(pct(r.believableFrac))")
    }
}

// --- Section 2: combined, mid-range and worst-case realistic --------------------------------------
print("")
print("== combined scenarios ==")
let midCombined = Perturbation(dPlaneY: 0.01, dAxisDeg: 2, dWidth: 0.01,
                                drift: SIMD3(1, 0, 1) / Float(2).squareRoot() * 0.02)
let worstCombined = Perturbation(dPlaneY: 0.03, dAxisDeg: 5, dWidth: 0.01,
                                  drift: SIMD3(1, 0, 1) / Float(2).squareRoot() * 0.05)
for (label, p) in [("mid-range (1cm plane, 2deg axis, 1cm dims, 2cm drift)", midCombined),
                    ("worst realistic (3cm plane, 5deg axis, 1cm dims, 5cm drift)", worstCombined)] {
    let r = evaluate(p)
    print("\(label): max \(fmt(r.maxErr))m  mean \(fmt(r.meanErr))m  believable \(pct(r.believableFrac))")
}

// --- Section 3: Monte Carlo over the full realistic range, all factors independent ----------------
print("")
print("== monte carlo: 500 trials, all factors sampled independently within their stated range ==")
var trialMax: [Float] = []
for _ in 0..<500 {
    let dPlaneY = Float.random(in: -0.03...0.03, using: &rng)
    let dAxis = Float.random(in: -5...5, using: &rng)
    let dWidth = Float.random(in: -0.01...0.01, using: &rng)
    let driftMag = Float.random(in: 0.01...0.05, using: &rng)
    let driftAngle = Float.random(in: 0..<(2 * .pi), using: &rng)
    let drift = SIMD3(cos(driftAngle), 0, sin(driftAngle)) * driftMag
    let r = evaluate(Perturbation(dPlaneY: dPlaneY, dAxisDeg: dAxis, dWidth: dWidth, drift: drift))
    trialMax.append(r.maxErr)
}
trialMax.sort()
func percentile(_ p: Double) -> Float { trialMax[min(trialMax.count - 1, Int(Double(trialMax.count) * p))] }
print("max corner error across trials: median \(fmt(percentile(0.5)))m  p95 \(fmt(percentile(0.95)))m  worst \(fmt(trialMax.last!))m")

// --- Section 4: the gate assertion -----------------------------------------------------------------
// Threshold picked from Section 1: at the low end of every stated range (0.5cm plane, 1deg axis,
// 1cm dims, 1cm drift) the overlay must stay within 2.5 cm of truth on every corner. That is looser
// than "sells the demo" (a few mm) but tighter than "looks broken" (3cm+) — see the report for why
// axis error is what eats the budget.
print("")
print("== gate assertion ==")
let gate = Perturbation(dPlaneY: 0.005, dAxisDeg: 1, dWidth: 0.01, drift: SIMD3(1, 0, 0) * 0.01)
let gateResult = evaluate(gate)
let threshold: Float = 0.025
assert(gateResult.maxErr < threshold,
       "regression: 0.5cm plane + 1deg axis + 1cm dims + 1cm drift now costs \(gateResult.maxErr)m, over \(threshold)m")
print("with 0.5cm plane, 1deg axis, 1cm dims, 1cm drift: max corner error \(fmt(gateResult.maxErr))m < \(threshold)m — ok")
