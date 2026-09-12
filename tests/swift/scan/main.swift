// Simulation of the AR scanner's geometry on Linux: synthetic ARKit scenes through the real
// convexHull / minAreaRect / fitBox / connectedCluster / densify / heightMap / ScannedItem.
// Run with tests/swift/scan/run.sh. Non-zero exit = a check failed.
import Foundation
#if canImport(simd)
import simd
#endif

let planeY: Float = 0.73  // table at a non-zero height, as in a real room
let noise: Float = 0.005  // LiDAR jitter, +-5 mm per mesh vertex

/// `ceil(span / cell)` done in Double, so the harness does not repeat the Float rounding bug it checks.
func cells(_ span: Float, _ cell: Float) -> Int { max(1, Int((Double(span) / Double(cell) - 1e-6).rounded(.up))) }

/// One tap: mesh -> densified cloud -> cluster from the seed -> box -> heightmap.
func tap(_ tris: [Tri], seed: SIMD3<Float>, noise: Float = 0, padding: Float = paddingMeters)
    -> (pts: [SIMD3<Float>], cluster: [SIMD3<Float>], box: BoxFit, hm: [[Float]]) {
    let pts = scan(tris, planeY: planeY, noise: noise)
    let cluster = connectedCluster(pts, seed: seed, cell: clusterCellMeters)
    let box = fitBox(points: cluster, planeY: planeY, padding: padding)!
    return (pts, cluster, box, heightMap(points: cluster, box: box, planeY: planeY, cell: shapeCellMeters))
}

// --- 1. boxes of several sizes and angles, with noise ------------------------------------
// fitBox measures a trimmed extent now, so symmetric jitter can no longer only inflate the box.
// What is left is +padding plus the sliver of the jitter cloud a fixed 2% trim cannot reach —
// about +2 mm a side, against the +5 mm (a whole jitter amplitude) a raw min/max used to cost.
let bias = paddingMeters + 0.008
for (w, d, h) in [(Float(0.20), Float(0.10), Float(0.05)),
                  (Float(0.30), Float(0.22), Float(0.12)),
                  (Float(0.08), Float(0.06), Float(0.15))] {
    for yaw in [Float(0), 30, 45, 89] {
        let c = SIMD2<Float>(1.0, 2.0)
        let r = tap(boxTris(w, d, h, at: c, yaw: yaw, planeY: planeY),
                    seed: SIMD3(c.x, planeY + h, c.y), noise: noise)
        let got = [r.box.width, r.box.depth].sorted(), want = [w, d].sorted()
        let errs = [got[0] - want[0], got[1] - want[1], r.box.height - h]
        record("box \(cm(w))x\(cm(d))x\(cm(h)) @\(Int(yaw))°",
               "\(cm(want[0]))x\(cm(want[1]))x\(cm(h))",
               "\(cm(got[0]))x\(cm(got[1]))x\(cm(r.box.height))",
               "\(mm(errs[0]))/\(mm(errs[1]))/\(mm(errs[2])) mm")
        for e in errs { check(abs(e) <= bias, "dim error \(mm(e)) mm on \(cm(w))x\(cm(d)) @\(yaw)°") }
        check(abs(r.box.center.x - c.x) < 0.01 && abs(r.box.center.z - c.y) < 0.01,
              "centre \(r.box.center) off (\(c.x), \(c.y))")
        check(abs(r.box.center.y - (planeY + h / 2)) < 0.01, "centre y \(r.box.center.y)")
        check(r.hm.count == cells(r.box.width, shapeCellMeters)
              && r.hm[0].count == cells(r.box.depth, shapeCellMeters),
              "grid \(r.hm.count)x\(r.hm[0].count) for \(cm(r.box.width))x\(cm(r.box.depth)) cm box")
        // Interior cells sit on the top face; the border ring is partly wall / partly outside.
        let inner = r.hm[2..<(r.hm.count - 2)].flatMap { $0[2..<($0.count - 2)] }
        check(inner.allSatisfy { abs($0 - h) <= noise + 0.002 },
              "top face cells \(cm(inner.min()!))–\(cm(inner.max()!)) cm, want \(cm(h))")
        // The trimmed box is smaller than the cloud, so the points it excluded clamp into the
        // border ring. That ring must still read the object's top, not 0 and not a runaway.
        let border = r.hm[0] + r.hm[r.hm.count - 1] + r.hm.map { $0[0] } + r.hm.map { $0[$0.count - 1] }
        check(border.allSatisfy { $0 > 0 && $0 <= h + noise + 0.002 },
              "border cells \(cm(border.min()!))–\(cm(border.max()!)) cm, want \(cm(h))")
    }
}

// Heavier jitter (±10 mm). The trim is a fixed share of the cloud, so the sliver it cannot
// reach scales with the noise — but it is still the trimmed extent, not the raw extremes.
let heavy: Float = 0.010
let hv = tap(boxTris(0.20, 0.10, 0.05, at: SIMD2(1, 2), yaw: 20, planeY: planeY),
             seed: SIMD3(1, planeY + 0.05, 2), noise: heavy)
let hvGot = [hv.box.width, hv.box.depth].sorted()
let hvErrs = [hvGot[0] - 0.10, hvGot[1] - 0.20, hv.box.height - 0.05]
record("box 20.0x10.0x5.0 @20°, ±10 mm", "10.0x20.0x5.0",
       "\(cm(hvGot[0]))x\(cm(hvGot[1]))x\(cm(hv.box.height))",
       "\(mm(hvErrs[0]))/\(mm(hvErrs[1]))/\(mm(hvErrs[2])) mm")
for e in hvErrs { check(abs(e) <= paddingMeters + 0.012, "±10 mm jitter: dim error \(mm(e)) mm") }

// One mesh spike 5 cm outside the object. fitBox is handed the cloud directly: connectedCluster
// would drop a point this far out, and the point of the check is that fitBox no longer needs it to.
let clean = scan(boxTris(0.20, 0.10, 0.05, at: SIMD2(1, 2), yaw: 0, planeY: planeY), planeY: planeY, noise: noise)
let cleanBox = fitBox(points: clean, planeY: planeY, padding: paddingMeters)!
let spikeBox = fitBox(points: clean + [SIMD3(1.15, planeY + 0.10, 2.0)],
                      planeY: planeY, padding: paddingMeters)!
let spikeErrs = [spikeBox.width - cleanBox.width, spikeBox.depth - cleanBox.depth,
                 spikeBox.height - cleanBox.height]
record("one 5 cm mesh spike", "\(cm(cleanBox.width))x\(cm(cleanBox.depth))x\(cm(cleanBox.height))",
       "\(cm(spikeBox.width))x\(cm(spikeBox.depth))x\(cm(spikeBox.height))",
       "\(mm(spikeErrs[0]))/\(mm(spikeErrs[1]))/\(mm(spikeErrs[2])) mm")
for e in spikeErrs { check(abs(e) < 0.001, "a single spike moved the box by \(mm(e)) mm") }

// The original tests/main.swift case: an exact multiple of the cell must not grow a phantom row.
let exact = tap(boxTris(0.10, 0.20, 0.05, at: SIMD2(1, 2), yaw: 30, planeY: planeY),
                seed: SIMD3(1, planeY + 0.05, 2), padding: 0)
let exactOK = [10, 20].contains(exact.hm.count) && [10, 20].contains(exact.hm[0].count)
record("exact multiple 10x20 @30°, pad 0", "10x20 cells",
       "\(exact.hm.count)x\(exact.hm[0].count) cells", exactOK ? "-" : "phantom row")
check(exactOK,
      "grid \(exact.hm.count)x\(exact.hm[0].count), want 10x20")
check(exact.hm.allSatisfy { $0.allSatisfy { abs($0 - 0.05) < 0.005 } }, "clean box heightmap not flat")

// --- 2. neighbours ------------------------------------------------------------------------
// Swept over sub-cell phases: connectedCluster now floods by point distance, so whether a
// gap wider than `clusterCellMeters` separates no longer depends on where the objects sit —
// unlike the old world-fixed grid, every phase must agree. 0.03 (3 cm) is the case the gate's
// own WARN row used to call out as a known limitation.
for gap in [Float(0.10), 0.04, 0.03, 0.02, 0.01] {
    var merged = 0
    for phase in [Float(0), 0.005, 0.01, 0.015] {
        let c = SIMD2<Float>(1.0 + phase, 2.0)
        let mine = boxTris(0.20, 0.10, 0.05, at: c, yaw: 0, planeY: planeY)
        let near = boxTris(0.06, 0.06, 0.05, at: SIMD2(c.x + 0.10 + gap + 0.03, c.y), yaw: 0, planeY: planeY)
        let alone = scan(mine, planeY: planeY).count
        let both = connectedCluster(scan(mine + near, planeY: planeY),
                                    seed: SIMD3(c.x, planeY + 0.05, c.y), cell: clusterCellMeters).count
        if both != alone { merged += 1 }
    }
    record("neighbour \(cm(gap)) cm away", gap > clusterCellMeters ? "excluded" : "merged",
           "\(4 - merged)/4 phases excluded", merged == 0 ? "-" : "\(merged)/4 merged")
    // A gap wider than the cluster radius must separate at every phase now (that is exactly
    // the bug this fixes); a gap narrower than it must still merge at every phase, same as before.
    check(gap > clusterCellMeters ? merged == 0 : merged == 4, "gap \(cm(gap)) cm: \(merged)/4 phases merged")
}

// A single long object, spanning many multiples of `clusterCellMeters`, must not fragment:
// its own densified samples are always <= `shapeCellMeters / 2` apart, well inside
// `clusterCellMeters`, so one tap must recover the whole object end to end, not a piece of it.
let long = boxTris(0.50, 0.06, 0.05, at: SIMD2(1.0, 2.0), yaw: 0, planeY: planeY)
let longPts = scan(long, planeY: planeY)
let longCluster = connectedCluster(longPts, seed: SIMD3(1.0, planeY + 0.05, 2.0), cell: clusterCellMeters)
record("long object (50 cm, one tap)", "\(longPts.count) pts kept",
       "\(longCluster.count) pts kept", longCluster.count == longPts.count ? "-" : "fragmented")
check(longCluster.count == longPts.count, "long object fragmented: \(longCluster.count)/\(longPts.count)")

// --- 3. L-shape and an open-top box --------------------------------------------------------
let ell = tap(boxTris(0.20, 0.10, 0.05, at: SIMD2(1.0, 1.95), yaw: 0, planeY: planeY)
              + boxTris(0.10, 0.10, 0.05, at: SIMD2(0.95, 2.05), yaw: 0, planeY: planeY),
              seed: SIMD3(1.0, planeY + 0.05, 1.95))
let flatL = ell.hm.flatMap { $0 }, holeL = flatL.filter { $0 == 0 }.count
record("L-shape missing quadrant", "25% empty",
       String(format: "%.0f%% empty", 100.0 * Double(holeL) / Double(flatL.count)), "-")
check(holeL > flatL.count / 5 && holeL < flatL.count / 3, "L-shape hole \(holeL)/\(flatL.count)")
check(flatL.filter { $0 > 0 }.allSatisfy { abs($0 - 0.05) < 0.006 }, "L-shape heights")

// Open-top box (shoe / open suitcase): a 3 cm rim at 12 cm, interior floor at 3 cm.
let (ow, od, oh, wall, floorH): (Float, Float, Float, Float, Float) = (0.30, 0.20, 0.12, 0.03, 0.03)
var openTris = boxTris(ow, od, oh, at: SIMD2(1.0, 2.0), yaw: 0, planeY: planeY)  // outer shell + lid
openTris.removeFirst(quadTris(SIMD3(0, 0, 0), SIMD3(ow, 0, 0), SIMD3(0, 0, od)).count)  // drop the lid
let o0 = SIMD3<Float>(1.0 - ow / 2, planeY, 2.0 - od / 2)
let iw = ow - 2 * wall, id = od - 2 * wall
let rim = o0 + SIMD3(0, oh, 0), inner = o0 + SIMD3(wall, oh, wall)
openTris += quadTris(rim, SIMD3(ow, 0, 0), SIMD3(0, 0, wall))                       // rim, 4 sides
openTris += quadTris(rim + SIMD3(0, 0, od - wall), SIMD3(ow, 0, 0), SIMD3(0, 0, wall))
openTris += quadTris(rim + SIMD3(0, 0, wall), SIMD3(wall, 0, 0), SIMD3(0, 0, id))
openTris += quadTris(rim + SIMD3(ow - wall, 0, wall), SIMD3(wall, 0, 0), SIMD3(0, 0, id))
openTris += quadTris(inner, SIMD3(iw, 0, 0), SIMD3(0, floorH - oh, 0))              // inner walls
openTris += quadTris(inner + SIMD3(0, 0, id), SIMD3(iw, 0, 0), SIMD3(0, floorH - oh, 0))
openTris += quadTris(inner, SIMD3(0, 0, id), SIMD3(0, floorH - oh, 0))
openTris += quadTris(inner + SIMD3(iw, 0, 0), SIMD3(0, 0, id), SIMD3(0, floorH - oh, 0))
openTris += quadTris(o0 + SIMD3(wall, floorH, wall), SIMD3(iw, 0, 0), SIMD3(0, 0, id))  // interior floor
let open = tap(openTris, seed: SIMD3(1.0, planeY + oh, 2.0 - od / 2 + wall / 2), noise: noise)
let mid = open.hm.count / 2, midJ = open.hm[0].count / 2
record("open box rim vs interior", "\(cm(oh)) vs \(cm(floorH)) cm",
       "\(cm(open.hm[mid][1])) vs \(cm(open.hm[mid][midJ])) cm",
       mm(open.hm[mid][1] - oh) + "/" + mm(open.hm[mid][midJ] - floorH))
check(open.hm[mid][midJ] < open.hm[mid][1] - 0.05, "interior not lower than rim")
check(abs(open.hm[mid][midJ] - floorH) <= noise + 0.005, "interior floor \(cm(open.hm[mid][midJ])) cm")
check(abs(open.hm[mid][1] - oh) <= noise + 0.005, "rim \(cm(open.hm[mid][1])) cm")
print(ScannedItem(open.box, heights: open.hm, cell: shapeCellMeters).asciiMap)

// --- 4. a thin flat item (2 cm book) --------------------------------------------------------
let book = tap(boxTris(0.15, 0.10, 0.02, at: SIMD2(1.0, 2.0), yaw: 0, planeY: planeY),
               seed: SIMD3(1.0, planeY + 0.02, 2.0), noise: noise)
record("book 15x10x2 cm", "15.0x10.0x2.0",
       "\(cm(book.box.width))x\(cm(book.box.depth))x\(cm(book.box.height))",
       "\(mm(book.box.width - 0.15))/\(mm(book.box.depth - 0.10))/\(mm(book.box.height - 0.02)) mm")
check(!book.cluster.isEmpty, "book cluster empty — minHeightMeters ate it")
check(abs(book.box.height - 0.02) <= bias, "book height \(cm(book.box.height)) cm")
check(abs(book.box.width - 0.15) <= bias && abs(book.box.depth - 0.10) <= bias,
      "book footprint \(cm(book.box.width))x\(cm(book.box.depth))")

// --- 5. points exactly on the box boundary --------------------------------------------------
let unit = BoxFit(width: 0.20, depth: 0.10, height: 0.05, center: SIMD3(0, 0.025, 0), axis: SIMD3(1, 0, 0))
let edgeHM = heightMap(points: [SIMD3(0.10, 0.05, 0.05), SIMD3(-0.10, 0.05, -0.05),
                                SIMD3(0.10, 0.04, -0.05), SIMD3(-0.10, 0.04, 0.05)],
                       box: unit, planeY: 0, cell: 0.01)
let corners = [edgeHM[19][9], edgeHM[0][0], edgeHM[19][0], edgeHM[0][9]]
record("boundary points d == +-w/2", "4 corners set",
       "\(corners.filter { $0 > 0 }.count) corners set", corners.contains(0) ? "dropped" : "-")
check(edgeHM.count == 20 && edgeHM[0].count == 10, "edge grid \(edgeHM.count)x\(edgeHM[0].count)")
check(corners.allSatisfy { $0 > 0 }, "boundary point dropped: \(corners)")

// --- 6. degenerate input ---------------------------------------------------------------------
let line = (0...10).map { SIMD2<Float>(Float($0) * 0.01, 0.02) }
let dupes = Array(repeating: SIMD3<Float>(1, 0.8, 2), count: 100)
check(convexHull([]).isEmpty, "hull of nothing")
check(convexHull([SIMD2(1, 1)]).count == 1, "hull of one")
check(convexHull([SIMD2(1, 1), SIMD2(1, 1)]).count == 1, "hull of a duplicate pair")
check(convexHull([SIMD2(1, 1), SIMD2(2, 2)]).count == 2, "hull of two")
check(convexHull(line).count == 2, "hull of a line: \(convexHull(line).count)")
check(minAreaRect(line) == nil, "min-area rect of a line is not a rectangle")
check(minAreaRect([]) == nil && minAreaRect([SIMD2(1, 1)]) == nil, "min-area rect of < 2 points")
check(fitBox(points: [], planeY: 0, padding: 0) == nil, "fitBox of nothing")
check(fitBox(points: [SIMD3(1, 0.8, 2)], planeY: 0.7, padding: 0) == nil, "fitBox of one point")
check(fitBox(points: dupes, planeY: 0.7, padding: 0) == nil, "fitBox of duplicates")
check(fitBox(points: line.map { SIMD3($0.x, 0.8, $0.y) }, planeY: 0.7, padding: 0) == nil, "fitBox of a line")
record("degenerate input", "nil, no crash", "nil, no crash", "-")

// A heightmap can legitimately have an empty row (0-depth box) or a 0 height; asciiMap must survive.
let flatItem = ScannedItem(BoxFit(width: 0.02, depth: 0, height: 0, center: .zero, axis: SIMD3(1, 0, 0)),
                           heights: [[], [0, 0]], cell: 0.01)
check(flatItem.asciiMap == "\n  ", "asciiMap of an empty row: \(flatItem.asciiMap.debugDescription)")

// densify samples a triangle at no worse than `spacing`, counting its longest edge (the hypotenuse).
let tri = densify(SIMD3(0, 0, 0), SIMD3(0.1, 0, 0), SIMD3(0, 0, 0.1), spacing: 0.01)
let n = Int((0.1 * 2.0.squareRoot() / 0.01).rounded(.up))
check(tri.count == (n + 1) * (n + 2) / 2, "densify \(tri.count), want \((n + 1) * (n + 2) / 2)")
record("densify 10x10 cm triangle", "\((n + 1) * (n + 2) / 2) pts", "\(tri.count) pts", "-")

// The JSON the server is promised: dimensions present, unset labels omitted.
let encoded = String(data: try! JSONEncoder().encode(ScannedItem(book.box, heights: book.hm, cell: shapeCellMeters)),
                     encoding: .utf8)!
check(encoded.contains("\"dimensions\"") && !encoded.contains("\"label\""), encoded)

// --- 7. timing on a dense mesh ----------------------------------------------------------------
let big = scan(boxTris(0.45, 0.35, 0.16, at: SIMD2(1, 2), yaw: 20, planeY: planeY), planeY: planeY)
let t0 = Date()
let bigCluster = connectedCluster(big, seed: SIMD3(1, planeY + 0.16, 2), cell: clusterCellMeters)
let t1 = Date()
let bigBox = fitBox(points: bigCluster, planeY: planeY, padding: paddingMeters)!
let t2 = Date()
_ = heightMap(points: bigCluster, box: bigBox, planeY: planeY, cell: shapeCellMeters)
let t3 = Date()
func ms(_ a: Date, _ b: Date) -> String { String(format: "%.0f ms", b.timeIntervalSince(a) * 1000) }
record("\(big.count) pts (main thread)", "-",
       "cluster \(ms(t0, t1)), fit \(ms(t1, t2)), heightMap \(ms(t2, t3))", "total \(ms(t0, t3))")

printTable()
if !failures.isEmpty { print("\n\(failures.count) check(s) FAILED"); exit(1) }
print("\nscan simulation ok")
