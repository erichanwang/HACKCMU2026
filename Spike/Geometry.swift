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

/// Fit a box to world-space points sitting on a horizontal plane at `planeY`.
/// nil when there is no 2D footprint to fit (see `minAreaRect`), so the tap reports
/// "nothing above the table here" rather than shipping a degenerate item.
func fitBox(points: [SIMD3<Float>], planeY: Float, padding: Float) -> BoxFit? {
    guard !points.isEmpty else { return nil }
    let flat = points.map { SIMD2<Float>($0.x, $0.z) }
    guard let r = minAreaRect(flat) else { return nil }
    let height = points.map { $0.y - planeY }.max()! + padding
    return BoxFit(
        width: r.w + padding, depth: r.d + padding, height: height,
        center: SIMD3<Float>(r.center.x, planeY + height / 2, r.center.y),
        axis: r.axis.x < 0 ? SIMD3<Float>(-r.axis.x, 0, -r.axis.y) : SIMD3<Float>(r.axis.x, 0, r.axis.y))
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
    var h = Array(repeating: Array(repeating: Float(0), count: nj), count: ni)
    for p in points {
        let d = p - box.center
        // Clamped, not dropped: the box is fitted to these same points, so a point on the far
        // boundary (d == +width/2) indexes one past the last cell and would otherwise zero the
        // very row that defines the box's edge.
        let i = min(ni - 1, max(0, Int(((simd_dot(d, box.axis) + box.width / 2) / cell).rounded(.down))))
        let j = min(nj - 1, max(0, Int(((simd_dot(d, perp) + box.depth / 2) / cell).rounded(.down))))
        h[i][j] = max(h[i][j], p.y - planeY)
    }
    return h
}

/// Output of one scan, in metres (team contract). `dimensions` = [width, height, depth] of the bounding
/// box (X right, Y up, Z forward). `heights[i][j]` is the surface height at cell (i along width, j along
/// depth) — the object's real shape as seen from above. Label/rigidity are filled in by the server.
struct ScannedItem: Codable, Identifiable {
    var id = UUID().uuidString
    var suitcaseId: String?        // the suitcase this was scanned into; the server rejects items without one
    var dimensions: [Float]
    var cellSize: Float
    var heights: [[Float]]
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
