import Foundation
#if canImport(simd)
import simd
#endif  // Linux: tests/swift/SimdShim.swift supplies the few simd functions used here

struct BoxFit {
    var width: Float   // along `axis`
    var depth: Float   // perpendicular to `axis`, in-plane
    var height: Float
    var center: SIMD3<Float>  // world-space center of the box
    var axis: SIMD3<Float>    // world-space unit vector of the width edge
    var hull: [SIMD2<Float>] = []  // world-space (x, z) convex hull of the fitted points
}

/// Convex hull of 2D points (monotone chain), counter-clockwise, no duplicates.
func convexHull(_ pts: [SIMD2<Float>]) -> [SIMD2<Float>] {
    let p = Array(Set(pts)).sorted { $0.x == $1.x ? $0.y < $1.y : $0.x < $1.x }
    guard p.count > 2 else { return p }
    func cross(_ o: SIMD2<Float>, _ a: SIMD2<Float>, _ b: SIMD2<Float>) -> Float {
        (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
    }
    var lower: [SIMD2<Float>] = [], upper: [SIMD2<Float>] = []
    for q in p {
        while lower.count >= 2 && cross(lower[lower.count - 2], lower[lower.count - 1], q) <= 0 { lower.removeLast() }
        lower.append(q)
    }
    for q in p.reversed() {
        while upper.count >= 2 && cross(upper[upper.count - 2], upper[upper.count - 1], q) <= 0 { upper.removeLast() }
        upper.append(q)
    }
    return Array(lower.dropLast() + upper.dropLast())
}

/// Minimum-area bounding rectangle of 2D points. Returns (width, depth, center, unit axis of width edge),
/// or nil when the points have no 2D footprint at all — fewer than three hull points means empty,
/// a single point, duplicates only, or an exactly collinear line, and a zero-depth rectangle is
/// worse than no scan — `POST /items` rejects any dimension <= 0 and the scan is lost anyway.
/// ponytail: O(hull²) edge scan, fine for a few thousand points; rotating calipers if it ever matters.
func minAreaRect(_ pts: [SIMD2<Float>]) -> (w: Float, d: Float, center: SIMD2<Float>, axis: SIMD2<Float>)? {
    let hull = convexHull(pts)
    guard hull.count >= 3 else { return nil }
    var best: (area: Float, w: Float, d: Float, center: SIMD2<Float>, axis: SIMD2<Float>)?
    for i in 0..<hull.count {
        let e = hull[(i + 1) % hull.count] - hull[i]
        let len = simd_length(e)
        if len < 1e-6 { continue }
        let u = e / len, v = SIMD2<Float>(-u.y, u.x)
        var minU = Float.greatestFiniteMagnitude, maxU = -Float.greatestFiniteMagnitude
        var minV = minU, maxV = maxU
        for q in hull {
            let a = simd_dot(q, u), b = simd_dot(q, v)
            minU = min(minU, a); maxU = max(maxU, a); minV = min(minV, b); maxV = max(maxV, b)
        }
        let w = maxU - minU, d = maxV - minV, area = w * d
        if best == nil || area < best!.area {
            let c = u * ((minU + maxU) / 2) + v * ((minV + maxV) / 2)
            best = (area, w, d, c, u)
        }
    }
    return best.map { ($0.w, $0.d, $0.center, $0.axis) }
}

/// Share of samples dropped from each end of an extent. LiDAR jitter is symmetric, but a raw
/// min/max can only ever be pushed outward by it, so every box used to read about one jitter
/// amplitude too big per side (+15 mm per footprint dimension at ARKit's ±5 mm, on top of the
/// padding) and the packer then fit fewer items than the bag holds.
/// Calibration knob: raise it if real scans still read big, lower it if they read small.
/// ponytail: one fixed share for every shape, so ~2 mm a side survives at ±5 mm jitter and it
/// scales with the noise. The unbiased cut is half the share the object's own end face
/// contributes, which ranges 5–25% per side over the simulated shapes — above ~4% a flat item's
/// thin end face is gone and the trim starts eating real geometry. Estimate that share per side
/// (count the points within a cell of the extreme, minus the interior density) if 2 mm matters.
let extentTrimFraction: Float = 0.02

/// Low and high ends of `xs` with `extentTrimFraction` of the samples dropped from each end —
/// a percentile, so a handful of outward-jittered samples (or one mesh spike) cannot set the edge.
/// A percentile rather than a mean of the outermost k, which averages exactly the samples that
/// jittered furthest out and so sits further out still.
/// ponytail: full sort, O(n log n); nth-element selection would be O(n) if 50 k points gets tight.
func trimmedRange(_ xs: [Float]) -> (lo: Float, hi: Float) {
    let s = xs.sorted()
    let k = min((s.count - 1) / 2, Int(Float(s.count) * extentTrimFraction))
    return (s[k], s[s.count - 1 - k])
}

/// Window width for `rimHeight`'s search: a fixed physical size, not a share of the object's
/// own height, so a short item's top-face noise band (a few mm) isn't squeezed by a tiny window,
/// and a single far-off outlier (a stray mesh spike) can't widen it either -- both broke a
/// percentage-of-range window in practice. Wide enough to swallow real LiDAR jitter at any
/// object height; narrow enough to still separate an open lid's climb from the rim it hinges
/// off (Spike/ScanView.swift's own scans put a rim comfortably below this per side).
let rimBandMeters: Float = 0.015

/// Top of the height band holding the most points: the rim of a cavity (open suitcase) or the
/// top face (a closed item) -- whichever, that's where the bulk of the scan sits, in a tight
/// plateau. An open lid hinged off the back rim rises past it, but never joins its density,
/// so it never controls this band. A closed scan has nothing above its own rim, so nothing
/// above the returned height exists to drop -- there is no way for this to touch a closed scan.
/// ponytail: pure height signature, one fixed-width sliding window. A lid flopped nearly flat
/// against the rim (rising less than `rimBandMeters`) would sit inside the rim's own band and
/// survive; footprint-vs-rim segmentation would be needed to catch that if a real bag ever does it.
func rimHeight(_ heights: [Float]) -> Float {
    let s = heights.sorted()
    guard let hi = s.last else { return 0 }
    var loI = 0, bestCount = 0, bestHi = hi
    for hiI in 0..<s.count {
        while s[hiI] - s[loI] > rimBandMeters { loI += 1 }
        let count = hiI - loI + 1
        if count > bestCount { bestCount = count; bestHi = s[hiI] }
    }
    return bestHi
}

/// Fit a box to world-space points sitting on a horizontal plane at `planeY`.
/// nil when there is no 2D footprint to fit (see `minAreaRect`), so the tap reports
/// "nothing above the table here" rather than shipping a degenerate item.
/// `trimAboveRim` drops everything above the rim -- an open suitcase lid hinged in frame,
/// rather than the cavity itself. **Suitcase scans only.** An item has no lid, and the trim is
/// actively dangerous there: `rimHeight` picks the densest height band, so an item whose top
/// face was not densely captured (occlusion, or the far side never seen -- both of which
/// `scripts/ar_sim.py` models) has no density spike at its top, every band ties, and the lowest
/// one wins. A 22 cm bottle then fits as a 1.5 cm disc and packs as a coaster. See
/// tests/swift/lid for both directions.
func fitBox(points: [SIMD3<Float>], planeY: Float, padding: Float, trimAboveRim: Bool = false) -> BoxFit? {
    guard !points.isEmpty else { return nil }
    var points = points
    if trimAboveRim {
        let rim = rimHeight(points.map { $0.y - planeY })
        points = points.filter { $0.y - planeY <= rim }
    }
    let flat = points.map { SIMD2<Float>($0.x, $0.z) }
    guard let r = minAreaRect(flat) else { return nil }
    // The hull only picks the rectangle's axis — jitter barely turns it. The extents come from
    // every point's projection onto that axis pair, trimmed, not from the hull's own extremes.
    let v = SIMD2<Float>(-r.axis.y, r.axis.x)
    let (uLo, uHi) = trimmedRange(flat.map { simd_dot($0, r.axis) })
    let (vLo, vHi) = trimmedRange(flat.map { simd_dot($0, v) })
    let height = trimmedRange(points.map { $0.y - planeY }).hi + padding
    let c = r.axis * ((uLo + uHi) / 2) + v * ((vLo + vHi) / 2)
    return BoxFit(
        width: uHi - uLo + padding, depth: vHi - vLo + padding, height: height,
        center: SIMD3<Float>(c.x, planeY + height / 2, c.y),
        axis: r.axis.x < 0 ? SIMD3<Float>(-r.axis.x, 0, -r.axis.y) : SIMD3<Float>(r.axis.x, 0, r.axis.y),
        hull: convexHull(flat))
}

/// Vertices kept in a footprint sent to the server: enough for an L-shape or oval to read
/// as non-rectangular, small next to a raw scan's hull (which can run to hundreds of points
/// on a curved item) so the JSON payload stays tiny. "a dozen or two" per the task -- 16 splits
/// the difference.
let maxFootprintVertices = 16

/// Reduce a convex polygon (CCW, from `convexHull`) to at most `maxFootprintVertices` points
/// while staying CONSERVATIVE -- the result must still contain every point `hull` had. Dropping
/// a hull vertex and connecting its neighbours with a straight chord cuts area *out* of the
/// polygon (that's the whole point of hull simplification elsewhere), which is backwards here: a
/// footprint that no longer contains a real corner of the scanned object is worse than no
/// footprint at all (the solver would pack against a shape rotated/shrunk away from reality). So
/// after picking which vertices to drop (least-area-contribution first -- i.e. near-collinear
/// points go first), the whole polygon is scaled outward from its centroid by just enough to
/// swallow every point the original hull had, including the ones just dropped.
/// ponytail: picks vertices to drop by repeated least-area scan (O(n^2) over hull vertices, at
/// most a few hundred here); a proper Douglas-Peucker/Visvalingam-Whyatt run if that ever bites.
func decimateHull(_ hull: [SIMD2<Float>]) -> [SIMD2<Float>] {
    guard hull.count > maxFootprintVertices else { return hull }
    var kept = hull
    while kept.count > maxFootprintVertices {
        var worstI = 0, worstArea = Float.greatestFiniteMagnitude
        for i in 0..<kept.count {
            let a = kept[(i - 1 + kept.count) % kept.count], b = kept[i], c = kept[(i + 1) % kept.count]
            let area = abs((b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x))
            if area < worstArea { worstArea = area; worstI = i }
        }
        kept.remove(at: worstI)
    }
    let centroid = hull.reduce(SIMD2<Float>(0, 0), +) / Float(hull.count)
    var scale: Float = 1.0
    for p in hull {
        let d = p - centroid
        let r = simd_length(d)
        guard r > 1e-6 else { continue }
        let dir = d / r
        // Distance from the centroid to `kept`'s boundary along `dir`: intersect the ray
        // centroid + t*dir (t >= 0) against each edge `a -> b` (a, b relative to centroid).
        var boundaryR = Float.greatestFiniteMagnitude
        for i in 0..<kept.count {
            let a = kept[i] - centroid, edge = kept[(i + 1) % kept.count] - centroid - a
            let denom = edge.x * dir.y - edge.y * dir.x
            guard abs(denom) > 1e-9 else { continue }
            let t = (edge.x * a.y - edge.y * a.x) / denom
            let s = (dir.x * a.y - dir.y * a.x) / denom
            if t >= 0, s >= 0, s <= 1 { boundaryR = min(boundaryR, t) }
        }
        if boundaryR.isFinite, boundaryR > 1e-6 { scale = max(scale, r / boundaryR) }
    }
    scale *= 1.001  // float-error safety margin
    return kept.map { centroid + ($0 - centroid) * scale }
}

/// Keep only points connected to the seed through occupied `cell`-sized grid cells (8-neighbourhood).
/// Separates the tapped object from neighbours that are at least one empty cell away.
/// ponytail: the grid is fixed to the world, so separation is only guaranteed for gaps wider
/// than two cells (4 cm at ScanView's 0.02) — a 3 cm gap that straddles one cell boundary
/// leaves no empty cell and merges. Flood by point distance instead if that ever bites.
func connectedCluster(_ points: [SIMD3<Float>], seed: SIMD3<Float>, cell: Float) -> [SIMD3<Float>] {
    struct Key: Hashable { let x: Int, z: Int }
    func key(_ p: SIMD3<Float>) -> Key { Key(x: Int((p.x / cell).rounded(.down)), z: Int((p.z / cell).rounded(.down))) }
    var grid: [Key: [SIMD3<Float>]] = [:]
    for p in points { grid[key(p), default: []].append(p) }
    var frontier = [key(seed)], seen = Set(frontier), out: [SIMD3<Float>] = []
    while let k = frontier.popLast() {
        out += grid[k] ?? []
        for dx in -1...1 { for dz in -1...1 {
            let n = Key(x: k.x + dx, z: k.z + dz)
            if grid[n] != nil, !seen.contains(n) { seen.insert(n); frontier.append(n) }
        } }
    }
    return out
}

/// Sample a triangle's surface at roughly `spacing` intervals so sparse mesh vertices become a dense cloud.
func densify(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>, spacing: Float) -> [SIMD3<Float>] {
    let longest = max(simd_length(b - a), simd_length(c - b), simd_length(a - c))
    let n = max(1, Int((longest / spacing).rounded(.up)))
    var out: [SIMD3<Float>] = []
    for i in 0...n { for j in 0...(n - i) {
        let u = Float(i) / Float(n), v = Float(j) / Float(n)
        out.append(a + (b - a) * u + (c - a) * v)
    } }
    return out
}

/// Surface height above the table for each `cell`-sized square of the box footprint.
/// Indexed `[i][j]`: i along the box's width axis, j along its depth axis. Unobserved cells are 0.
/// ponytail: 2.5D — undercuts and overhangs are invisible from above; multi-view fusion if that ever matters.
func heightMap(points: [SIMD3<Float>], box: BoxFit, planeY: Float, cell: Float) -> [[Float]] {
    let perp = SIMD3<Float>(-box.axis.z, 0, box.axis.x)
    // ceil(span / cell) with a sliver of slack: in Float 0.2 / 0.01 is 20.000002,
    // and a bare ceil then ships an extra all-zero row on every exact multiple of the cell.
    func count(_ span: Float) -> Int { max(1, Int((span / cell - 1e-4).rounded(.up))) }
    let ni = count(box.width), nj = count(box.depth)
    // Index from the grid's own span, not the box's: that ceil rounds the grid up to a whole
    // cell, and measuring from the box edge would pile all of the rounding at the far end —
    // a dead last row on any box whose size is not a multiple of the cell.
    let spanI = Float(ni) * cell, spanJ = Float(nj) * cell
    var h = Array(repeating: Array(repeating: Float(0), count: nj), count: ni)
    for p in points {
        let d = p - box.center
        // Clamped, not dropped: the box is fitted to these same points, so a point on the far
        // boundary (d == +width/2) indexes one past the last cell and would otherwise zero the
        // very row that defines the box's edge.
        let i = min(ni - 1, max(0, Int(((simd_dot(d, box.axis) + spanI / 2) / cell).rounded(.down))))
        let j = min(nj - 1, max(0, Int(((simd_dot(d, perp) + spanJ / 2) / cell).rounded(.down))))
        h[i][j] = max(h[i][j], p.y - planeY)
    }
    return h
}

/// Output of one scan, in metres (team contract). `dimensions` = [width, height, depth] of the bounding
/// box (X right, Y up, Z forward). `heights[i][j]` is the surface height at cell (i along width, j along
/// depth) — the object's real shape as seen from above. Label/rigidity are filled in by the server.
/// `Equatable` is for SpikeApp's `.onChange(of: item)`; the synthesised `==` walks `heights`, a few
/// hundred floats, which is cheap enough next to one scan.
struct ScannedItem: Codable, Identifiable, Equatable {
    var id = UUID().uuidString
    var suitcaseId: String?        // the suitcase this was scanned into; the server rejects items without one
    var dimensions: [Float]
    var cellSize: Float
    var heights: [[Float]]
    // Convex local (x, z) polygon, METRES (this struct's team contract -- see `dimensions`
    // above -- docs/LIDAR_HULLS.md's own snippet is written against the older cm-valued
    // ScannedItem and would double-convert against today's struct), relative to the box
    // centre (width +x, depth +z) -- same frame/sign convention as physics/io.py's
    // `object_from_box_fit` local frame. nil for a plain box (degenerate or unavailable hull).
    var footprint: [[Float]]?
    var label: String?
    var labelSource: String?
    var labelStatus: String?       // "done", or "pending" while the server retries Grok in the background
    var description: String?       // one sentence from Grok
    var mass: Double?              // estimated kg from Grok, 0 = unknown
    var keepUpright: Bool?         // must stay this side up (liquids, open containers)
    var rigidity: String?
    var rigiditySource: String?
    var compressibility: Double?   // loose volume / squeezed volume, >= 1; 1 unless soft
    var compressibilitySource: String?
    var createdAt: String?

    init(_ box: BoxFit, heights: [[Float]], cell: Float) {
        dimensions = [box.width, box.height, box.depth]
        cellSize = cell
        self.heights = heights
        footprint = ScannedItem.footprint(from: box)
    }

    /// Local-frame footprint (metres, matching `dimensions`) from a `BoxFit`'s world-space
    /// hull: decimated (see `decimateHull`), then projected onto the box's own axes and
    /// clamped to its half-dimensions -- physics/geometry.py's `footprint_local` rejects
    /// (>1e-6) anything past that boundary, and the box itself is already the accepted outer
    /// envelope (its own `padding`/`trimmedRange` is where jitter tolerance lives, not here).
    /// nil when the hull has no real footprint (fewer than 3 points).
    static func footprint(from box: BoxFit) -> [[Float]]? {
        guard box.hull.count >= 3 else { return nil }
        let decimated = decimateHull(box.hull)
        let u = SIMD2(box.axis.x, box.axis.z), v = SIMD2(-u.y, u.x)
        let c = SIMD2(box.center.x, box.center.z)
        let hx = box.width / 2, hz = box.depth / 2
        return decimated.map { p in
            let d = p - c
            let x = min(hx, max(-hx, simd_dot(d, u)))
            let z = min(hz, max(-hz, simd_dot(d, v)))
            return [x, z]
        }
    }

    var width: Float { dimensions[0] }
    var height: Float { dimensions[1] }
    var depth: Float { dimensions[2] }

    /// Top-down ASCII view, one character per cell, darker = taller.
    var asciiMap: String {
        let ramp = Array(" .:-=+*#%@"), top = max(height, 0.0001)
        return heights.map { row in
            String(row.map { ramp[min(ramp.count - 1, Int($0 / top * Float(ramp.count - 1)))] })
        }.joined(separator: "\n")
    }
}
