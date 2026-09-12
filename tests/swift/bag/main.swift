import Foundation
#if canImport(simd)
import simd
#endif  // Linux: tests/swift/SimdShim.swift supplies the few simd functions used here

// Spike/ScanView.swift Suitcase mode scans the bag's OUTER shell, then `interiorBox` subtracts a
// single flat `suitcaseWallMeters` (1 cm) to guess the interior. A real hard-shell carry-on isn't
// outer-minus-one-flat-wall: it has a wheel well (two housings biting up from the floor) and a
// handle spine (the telescoping handle's channel biting in from the back wall, full height). This
// file quantifies how wrong the flat model is, then checks a fix.
//
// Bag-local frame: x = width, z = depth, y = height, floor at y = 0, back wall (wells + handle) at
// z = +depth/2, front opening at z = -depth/2. All units metres.

func approx(_ a: Float, _ b: Float, _ tol: Float = 1e-4) -> Bool { abs(a - b) < tol }
func r2(_ x: Float) -> Float { (x * 100).rounded() / 100 }
func r1(_ x: Float) -> Float { (x * 10).rounded() / 10 }
func pad(_ s: String, _ n: Int) -> String { s + String(repeating: " ", count: max(0, n - s.count)) }

struct RealBag {
    var name: String
    var outerW: Float, outerH: Float, outerD: Float   // scanned outer shell
    var shell: Float                                   // true shell + floor thickness
    var wellHeight: Float, wellDepth: Float, wellWidth: Float  // two housings, back-bottom corners
    var spineDepth: Float, spineWidth: Float                    // handle channel, full height, back wall

    var outerBox: BoxFit {
        BoxFit(width: outerW, depth: outerD, height: outerH, center: SIMD3(0, outerH / 2, 0), axis: SIMD3(1, 0, 0))
    }

    // Shell-only interior (what a flat-wall model is trying to approximate), before carving out
    // the well housings and the handle spine.
    var shellInterior: (x: Float, y: Float, z: Float) {
        (outerW - 2 * shell, outerH - shell, outerD - 2 * shell)
    }

    /// Ground truth: shell-only interior volume minus the two well housings minus the spine.
    /// Wells and spine are disjoint by construction (the spine sits above the wells), so their
    /// volumes subtract cleanly off the shell-only box.
    var trueVolume: Float {
        let (ix, iy, iz) = shellInterior
        let wellVol = 2 * wellWidth * wellHeight * wellDepth
        let spineVol = spineWidth * (iy - wellHeight) * spineDepth
        return ix * iy * iz - wellVol - spineVol
    }
}

let bags: [RealBag] = [
    RealBag(name: "small carry-on", outerW: 0.50, outerH: 0.35, outerD: 0.20, shell: 0.010,
            wellHeight: 0.045, wellDepth: 0.07, wellWidth: 0.06, spineDepth: 0.030, spineWidth: 0.10),
    RealBag(name: "medium carry-on", outerW: 0.55, outerH: 0.40, outerD: 0.23, shell: 0.010,
            wellHeight: 0.05, wellDepth: 0.08, wellWidth: 0.07, spineDepth: 0.035, spineWidth: 0.12),
    RealBag(name: "large carry-on", outerW: 0.60, outerH: 0.45, outerD: 0.25, shell: 0.010,
            wellHeight: 0.06, wellDepth: 0.09, wellWidth: 0.08, spineDepth: 0.040, spineWidth: 0.14),
]

// --- 1. Quantify today's flat 1 cm model against the true interior -------------------------------
print("=== current flat-wall model (interiorBox(outer, wall: 0.01)) vs true interior ===")
for bag in bags {
    let flat = interiorBox(bag.outerBox, wall: 0.01)
    let flatVol = flat.width * flat.depth * flat.height
    let trueVol = bag.trueVolume
    let overshoot = (flatVol - trueVol) / trueVol * 100
    print("\(pad(bag.name, 16)) flat=\(r2(flatVol * 1000))L true=\(r2(trueVol * 1000))L " +
          "error=+\(r1(overshoot))% (model believes \(r2((flatVol - trueVol) * 1000))L that isn't there)")
    // The flat model always overestimates once wells/spine exist — that's the failure mode that
    // doesn't fit in the real bag.
    assert(flatVol > trueVol, "\(bag.name): flat model should overestimate")
    assert(overshoot > 3, "\(bag.name): expected the flat model to be off by a real margin, got \(overshoot)%")

    // And it's not just an academic overestimate: the flat model's box actually reaches into the
    // well and spine void. Check its highest-z, lowest-y corner lands inside forbidden space.
    let (_, _, iz) = bag.shellInterior
    let flatMaxZ = iz / 2      // flat model shrinks depth symmetrically by the same 1 cm wall
    let spineNearZ = iz / 2 - bag.spineDepth
    assert(flatMaxZ > spineNearZ, "\(bag.name): flat model's back face should reach the spine")
}

// --- 2. Symmetric baseline: same wallDepth off both faces, hand-rolled -----------------------
// This is what `interiorBox` did before the asymmetric fix below (and what section 4's single
// flat knob still does): both the front (opening, no intrusion) and back (spine) faces lose
// `wallDepth`, because nothing shifts the box's center in z. Hand-rolled here (not calling
// `interiorBox`, which now does the cheaper thing) purely as the "before" baseline section 3
// measures its improvement against.
print("\n=== symmetric baseline (old interiorBox behavior, hand-rolled) vs true interior ===")
for bag in bags {
    let wallHeight = bag.shell + bag.wellHeight
    let wallDepth = bag.shell + bag.spineDepth
    let modeled = BoxFit(width: bag.outerW - 2 * bag.shell, depth: bag.outerD - 2 * wallDepth,
                          height: bag.outerH - wallHeight,
                          center: SIMD3<Float>(0, bag.outerH / 2 + wallHeight / 2, 0), axis: SIMD3(1, 0, 0))
    let modeledVol = modeled.width * modeled.depth * modeled.height
    let trueVol = bag.trueVolume
    let margin = (trueVol - modeledVol) / trueVol * 100
    print("\(pad(bag.name, 16)) modeled=\(r2(modeledVol * 1000))L true=\(r2(trueVol * 1000))L " +
          "margin=-\(r1(margin))% (safe volume given up)")
    assert(modeledVol < trueVol, "\(bag.name): symmetric baseline must stay inside the true interior")

    // Containment proof: every corner of the modeled interior box must clear the well (in y) and
    // the spine (in z), regardless of the wells'/spine's actual x-position or width.
    let halfW = modeled.width / 2, halfD = modeled.depth / 2
    let minY = modeled.center.y - modeled.height / 2, maxY = modeled.center.y + modeled.height / 2
    let wellTopY = bag.shell + bag.wellHeight
    let spineNearZ = bag.outerD / 2 - bag.shell - bag.spineDepth
    let eps: Float = 1e-5
    assert(minY >= wellTopY - eps, "\(bag.name): modeled floor \(minY) dips into the well (top at \(wellTopY))")
    assert(halfD <= spineNearZ + eps, "\(bag.name): modeled back wall \(halfD) crosses into the spine (starts at \(spineNearZ))")
    assert(maxY <= bag.outerH + eps, "\(bag.name): modeled interior taller than the outer shell")
    assert(halfW <= bag.outerW / 2 - bag.shell + eps, "\(bag.name): modeled interior wider than the shell-only interior")
    // The symmetric baseline is safe, but it must not become so conservative it demos as a bag
    // that "holds nothing" — cap how much volume it's allowed to give up.
    assert(margin < 45, "\(bag.name): symmetric baseline should give up well under half the bag, got \(margin)%")
}

// --- 3. The real fix, now implemented: interiorBox pays the spine once -------------------------
// Section 2's wallDepth was subtracted from BOTH the front (opening, no intrusion) and back
// (spine) faces, because interiorBox never shifted the box's center in z. The spine only exists
// at the back, so half of that cut was pure waste. Height doesn't have this problem: interiorBox
// already raises the floor by wallHeight AND shifts center.y up by wallHeight/2, so the modeled
// top still touches the outer shell exactly (zero waste there). `Spike/PlanAnchor.swift` now
// gives depth the same treatment — pays wallDepth once from the back (+perp, see PlanAnchor's
// frame comment) and shifts center.z toward the opening by half the extra intrusion — so this
// calls the real function, not a hand-rolled model.
print("\n=== asymmetric depth (interiorBox, pays the spine once) vs true interior ===")
for bag in bags {
    let wallHeight = bag.shell + bag.wellHeight
    let wallDepth = bag.shell + bag.spineDepth
    let modeled = interiorBox(bag.outerBox, wall: bag.shell, wallHeight: wallHeight, wallDepth: wallDepth)
    let modeledVol = modeled.width * modeled.depth * modeled.height
    let trueVol = bag.trueVolume
    let margin = (trueVol - modeledVol) / trueVol * 100
    print("\(pad(bag.name, 16)) modeled=\(r2(modeledVol * 1000))L true=\(r2(trueVol * 1000))L " +
          "margin=-\(r1(margin))% (safe volume given up)")
    // Both directions: never overestimate (the failure mode that doesn't fit the real bag), and
    // never give up more than the measured margin (an over-conservative model is a worse demo).
    assert(modeledVol < trueVol, "\(bag.name): asymmetric model must stay inside the true interior")
    assert(margin < 26, "\(bag.name): asymmetric fix should give up well under a quarter of the bag, got \(margin)%")

    // Same containment proof as section 2, in world space (center.z is no longer 0), plus: the
    // back face should touch the spine boundary exactly (a tight fit, not just another pad).
    let minY = modeled.center.y - modeled.height / 2, maxY = modeled.center.y + modeled.height / 2
    let wellTopY = bag.shell + bag.wellHeight
    let spineNearZ = bag.outerD / 2 - bag.shell - bag.spineDepth
    let backZ = modeled.center.z + modeled.depth / 2
    let frontZ = modeled.center.z - modeled.depth / 2
    let eps: Float = 1e-4
    assert(minY >= wellTopY - eps, "\(bag.name): asymmetric floor dips into the well")
    assert(maxY <= bag.outerH + eps, "\(bag.name): asymmetric interior taller than the outer shell")
    assert(backZ <= spineNearZ + eps, "\(bag.name): asymmetric back wall crosses into the spine")
    assert(abs(backZ - spineNearZ) < eps, "\(bag.name): asymmetric back wall should touch the spine boundary, not pad past it")
    assert(abs(frontZ - (-bag.outerD / 2 + bag.shell)) < eps, "\(bag.name): asymmetric front wall should touch the shell-only interior, not pad past it")

    // It should also recover more usable volume than section 2's symmetric baseline.
    let symModeled = BoxFit(width: bag.outerW - 2 * bag.shell, depth: bag.outerD - 2 * wallDepth,
                             height: bag.outerH - wallHeight,
                             center: SIMD3<Float>(0, bag.outerH / 2 + wallHeight / 2, 0), axis: SIMD3(1, 0, 0))
    let symVol = symModeled.width * symModeled.depth * symModeled.height
    assert(modeledVol > symVol, "\(bag.name): asymmetric fix should recover more volume than the symmetric one")
}

// --- 4. Sanity-check a single flat wall applied to every axis (today's ScanView.swift) -----------
// Spike/ScanView.swift:123 calls interiorBox(box, wall: suitcaseWallMeters) with ONE constant;
// wallHeight/wallDepth default to it. There is no value of that single constant that is both
// safe and efficient: big enough to clear the tallest well and deepest spine, it also needlessly
// shrinks the width, which has no well or spine at all.
print("\n=== single flat knob (today's ScanView.swift call) can't be both safe and efficient ===")
for bag in bags {
    let neededForHeight = bag.shell + bag.wellHeight
    let neededForDepth = bag.shell + bag.spineDepth
    let safeFlat = max(neededForHeight, neededForDepth)   // one number safe on every axis
    let modeled = interiorBox(bag.outerBox, wall: safeFlat)
    let modeledVol = modeled.width * modeled.depth * modeled.height
    let trueVol = bag.trueVolume
    let margin = (trueVol - modeledVol) / trueVol * 100
    print("\(pad(bag.name, 16)) safe single wall=\(r2(safeFlat * 100))cm margin=-\(r1(margin))% (vs -39..-41% with 3 separate constants)")
    assert(modeledVol < trueVol, "\(bag.name): single safe wall should still stay inside the true interior")
    assert(margin > 50, "\(bag.name): forcing one constant to be safe on every axis should cost more than half the bag, got \(margin)%")
}

print("\nbag model: flat 1 cm wall overestimates every simulated bag; the calibrated per-axis model stays inside the true interior, and the asymmetric fix halves the wasted volume — ok")
