import Foundation
import simd

// Host tests for Spike/Voxels.swift. Pure geometry, no ARKit, so they run on the Mac:
//   swiftc -O Spike/Voxels.swift tests/voxels.swift -o /tmp/voxtest && /tmp/voxtest

func check(_ cond: Bool, _ msg: @autoclosure () -> String) {
    if !cond { FileHandle.standardError.write(Data(("FAIL: " + msg() + "\n").utf8)); exit(1) }
}
func approxEq(_ a: Float, _ b: Float, _ tol: Float = 1e-4) -> Bool { abs(a - b) < tol }

// Surface samples on a 10 x 6 x 4 cm box centred at (1, 0.75, 2).
func boxSurface(centre: SIMD3<Float>, size: SIMD3<Float>, step: Float = 0.004) -> [SIMD3<Float>] {
    var pts: [SIMD3<Float>] = []
    let lo = centre - size / 2, hi = centre + size / 2
    var x = lo.x
    while x <= hi.x {
        var y = lo.y
        while y <= hi.y {
            var z = lo.z
            while z <= hi.z {
                let onShell = abs(x - lo.x) < step || abs(x - hi.x) < step
                    || abs(y - lo.y) < step || abs(y - hi.y) < step
                    || abs(z - lo.z) < step || abs(z - hi.z) < step
                if onShell { pts.append(SIMD3(x, y, z)) }
                z += step
            }
            y += step
        }
        x += step
    }
    return pts
}

func runVoxelTests() {
    let centre = SIMD3<Float>(1, 0.75, 2), size = SIMD3<Float>(0.10, 0.06, 0.04)
    let surface = boxSurface(centre: centre, size: size)

    // --- basic accumulation -------------------------------------------------------
    var g = VoxelGrid(voxelSize: 0.005)
    for p in surface { g.add(p) }
    check(!g.isEmpty, "grid should not be empty")
    let b = g.bounds!
    check(approxEq(b.max.x - b.min.x, size.x, 0.006), "width \(b.max.x - b.min.x)")
    check(approxEq(b.max.y - b.min.y, size.y, 0.006), "height \(b.max.y - b.min.y)")
    check(approxEq(b.max.z - b.min.z, size.z, 0.006), "depth \(b.max.z - b.min.z)")

    // --- merging is order independent and additive --------------------------------
    var a1 = VoxelGrid(voxelSize: 0.005), a2 = VoxelGrid(voxelSize: 0.005)
    for (i, p) in surface.enumerated() { if i % 2 == 0 { a1.add(p) } else { a2.add(p) } }
    var merged = a1; merged.merge(a2)
    var reversed = a2; reversed.merge(a1)
    check(merged.count == g.count, "merged \(merged.count) vs single-pass \(g.count)")
    check(merged.count == reversed.count, "merge must commute")

    // --- sub-voxel centroid beats lattice resolution ------------------------------
    // One voxel fed many samples around a known point must recover that point far more
    // precisely than the 5 mm cell it lives in.
    var fine = VoxelGrid(voxelSize: 0.05)
    let truth = SIMD3<Float>(0.123, 0.456, 0.789)
    for i in 0..<400 {
        let t = Float(i) / 400 * 2 * .pi
        fine.add(truth + SIMD3(cos(t), sin(t), cos(2 * t)) * 0.004)  // jitter inside one voxel
    }
    let recovered = fine.cells.values.first!.centroid
    check(simd_length(recovered - truth) < 0.001,
          "sub-voxel centroid off by \(simd_length(recovered - truth)) m")

    // --- pruning removes single-sample noise --------------------------------------
    var noisy = VoxelGrid(voxelSize: 0.005)
    for p in surface { noisy.add(p); noisy.add(p) }             // real surface, seen twice
    // Isolated one-off noise. Spaced well beyond one voxel so each sample lands in its
    // own cell with a single observation -- which is exactly what stray LiDAR hits do.
    for i in 0..<200 {
        let t = Float(i)
        noisy.add(SIMD3(1.4 + t * 0.02, 0.9 + t * 0.011, 2.4 + t * 0.017))
    }
    let before = noisy.count
    let clean = noisy.pruned(minObservations: 2)
    check(clean.count < before, "pruning should drop voxels")
    let cb = clean.bounds!
    check(approxEq(cb.max.x - cb.min.x, size.x, 0.006),
          "pruned width \(cb.max.x - cb.min.x) - noise survived")

    // --- 3D components separate an object from the surface under it ---------------
    // The case a 2D footprint fill gets wrong: a slab directly beneath the box overlaps it
    // completely in plan view, so only true 3D connectivity can tell them apart.
    var scene = VoxelGrid(voxelSize: 0.005)
    for p in surface { scene.add(p) }
    let slab = boxSurface(centre: SIMD3(1, 0.70, 2), size: SIMD3(0.40, 0.01, 0.40))
    for p in slab { scene.add(p) }
    let obj = scene.component(containing: centre)
    check(obj.count < scene.count, "component must exclude the slab")
    let ob = obj.bounds!
    check(approxEq(ob.max.x - ob.min.x, size.x, 0.008),
          "component width \(ob.max.x - ob.min.x) - it swallowed the surface below")
    check(ob.min.y > 0.71, "component reached down into the slab (min y \(ob.min.y))")

    // --- nearest-occupied tolerates a tap that misses ------------------------------
    check(scene.nearestOccupied(to: centre + SIMD3(0, 0.2, 0)) != nil, "should find something")
    check(VoxelGrid(voxelSize: 0.005).nearestOccupied(to: .zero) == nil, "empty grid -> nil")

    // --- colour round trips through the payload ------------------------------------
    var col = VoxelGrid(voxelSize: 0.01)
    col.add(SIMD3(0, 0, 0), colour: SIMD3(1, 0, 0))
    col.add(SIMD3(0, 0, 0), colour: SIMD3(0, 0, 1))   // same voxel, averages to purple
    col.add(SIMD3(0.5, 0, 0), colour: SIMD3(0, 1, 0))
    col.add(SIMD3(1.0, 0, 0))                          // geometry only, no colour
    check(approxEq(col.colourCoverage, 2.0 / 3.0, 0.01), "colour coverage \(col.colourCoverage)")
    let pay = col.payload()
    check(pay.indices.count == col.count * 3, "indices \(pay.indices.count) for \(col.count) voxels")
    check(pay.observations.count == col.count, "observations length")
    check(pay.colours.count == col.count * 3, "colours length")
    check(pay.voxelSize == 0.01, "voxel size preserved")
    // The averaged red+blue voxel must come back purple-ish, not red or blue.
    let first = (0..<col.count).first { pay.indices[$0 * 3] == 0 && pay.indices[$0 * 3 + 1] == 0 && pay.indices[$0 * 3 + 2] == 0 }!
    check(pay.colours[first * 3] > 100 && pay.colours[first * 3 + 2] > 100,
          "averaged colour \(pay.colours[first * 3]),\(pay.colours[first * 3 + 1]),\(pay.colours[first * 3 + 2])")
    // Deterministic voxel ordering, so the same scan always packs the same way.
    // Asserted on the arrays, not on encoded bytes: JSONEncoder does not promise a
    // stable key order, and that is its business rather than the payload's.
    let again = col.payload()
    check(again.indices == pay.indices, "voxel order must be stable")
    check(again.colours == pay.colours && again.observations == pay.observations,
          "payload arrays must be stable")

    // --- capture guidance ----------------------------------------------------------
    var cov = ViewCoverage(azimuthBins: 12)
    check(cov.covered == 0 && cov.total == 12, "coverage starts empty")
    cov.record(direction: SIMD3(1, 0, 0))
    check(cov.covered == 1, "one direction recorded")
    cov.record(direction: SIMD3(1, 0, 0))
    check(cov.covered == 1, "same direction must not double count")
    check(cov.nextHint(from: SIMD3(1, 0, 0)) != nil, "should suggest somewhere to go")
    // Opposite sides of the object are different slices of the ring.
    check(cov.bin(for: SIMD3(1, 0, 0)) != cov.bin(for: SIMD3(-1, 0, 0)), "azimuth must bin apart")
    // A level view is not a top view; straight overhead is.
    check(!cov.isTopView(SIMD3(1, 0, 0)), "horizontal is not a top view")
    check(cov.isTopView(SIMD3(0, 1, 0)), "overhead is a top view")

    // THE POINT OF THIS MODEL: one lap at eye level closes the ring and reads 100%.
    var lap = ViewCoverage(azimuthBins: 12)
    for a in 0..<12 {
        let az = (Float(a) + 0.5) / 12 * 2 * .pi
        lap.record(direction: SIMD3(cos(az), 0.15, sin(az)))   // slightly above level, as a person holds a phone
    }
    check(approxEq(lap.fraction, 1.0), "one orbit must read as complete, got \(lap.fraction)")
    check(!lap.sawTop, "a level orbit should not count as having seen the top")
    // The only thing left to ask for is the top.
    let hint = lap.nextHint(from: SIMD3(1, 0, 0))
    check(hint != nil && hint!.contains("above"), "should now ask for a top view, got \(hint ?? "nil")")
    lap.record(direction: SIMD3(0, 1, 0))
    check(lap.sawTop && lap.isComplete, "ring plus a top view is complete")
    check(lap.nextHint(from: SIMD3(1, 0, 0)) == nil, "no hint once complete")

    // --- camera projection ----------------------------------------------------------
    // Camera one metre back along +Z from the origin, looking down -Z (ARKit convention),
    // so world -> camera is a pure translation of -1 in z.
    let K = simd_float3x3(SIMD3(600, 0, 0), SIMD3(0, 600, 0), SIMD3(320, 240, 1))
    var view = matrix_identity_float4x4
    view.columns.3 = SIMD4(0, 0, -1, 1)
    let frame = PosedFrame(viewMatrix: view, intrinsics: K, imageSize: SIMD2(640, 480))

    // A point at the origin sits dead centre.
    let centrePx = frame.project(.zero)!
    check(approxEq(centrePx.x, 320, 0.01) && approxEq(centrePx.y, 240, 0.01), "centre \(centrePx)")
    // +X in the world is to the right in the image.
    check(frame.project(SIMD3(0.1, 0, 0))!.x > 320, "world +x should be right of centre")
    // +Y in the world is UP, which is a SMALLER v because image rows run downward.
    // Getting this sign wrong flips every texture vertically, so it is worth pinning.
    check(frame.project(SIMD3(0, 0.1, 0))!.y < 240, "world +y should be above centre")
    // fx * (0.1 / 1.0) = 60 px off centre at one metre.
    check(approxEq(frame.project(SIMD3(0.1, 0, 0))!.x, 380, 0.01), "expected 380 px")
    // Behind the camera, and far outside the frame, both reject.
    check(frame.project(SIMD3(0, 0, 2)) == nil, "points behind the camera must reject")
    check(frame.project(SIMD3(5, 0, 0)) == nil, "points outside the frame must reject")
    // The camera sits at +Z, so that is the direction an object is seen from.
    let dir = frame.directionToCamera(from: .zero)
    check(dir.z > 0.99, "direction to camera \(dir)")

    print("voxels ok: \(g.count) voxels, \(clean.count) after pruning, colour coverage \(col.colourCoverage)")

}
