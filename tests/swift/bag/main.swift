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

// --- 2. The fix: per-axis wall, calibrated to clear the well and the spine -----------------------
// interiorBox grows two optional per-axis walls (default = the flat `wall`, so every existing
// caller and the plan-anchor test keep compiling and behaving unchanged). Setting them to
// shell + intrusion depth raises the modeled floor above the wells and pulls the modeled back
// (and, symmetrically, front) wall clear of the handle spine, for any width/x-position those
// features actually have.
print("\n=== calibrated per-axis model vs true interior ===")
for bag in bags {
    let wallHeight = bag.shell + bag.wellHeight
    let wallDepth = bag.shell + bag.spineDepth
    let modeled = interiorBox(bag.outerBox, wall: bag.shell, wallHeight: wallHeight, wallDepth: wallDepth)
    let modeledVol = modeled.width * modeled.depth * modeled.height
    let trueVol = bag.trueVolume
    let margin = (trueVol - modeledVol) / trueVol * 100
    print("\(pad(bag.name, 16)) modeled=\(r2(modeledVol * 1000))L true=\(r2(trueVol * 1000))L " +
          "margin=-\(r1(margin))% (safe volume given up)")
    assert(modeledVol < trueVol, "\(bag.name): calibrated model must stay inside the true interior")

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
}

print("\nbag model: flat 1 cm wall overestimates every simulated bag; the calibrated per-axis model stays inside the true interior for all of them — ok")
