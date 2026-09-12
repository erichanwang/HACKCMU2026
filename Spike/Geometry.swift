import Foundation
import simd

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

/// Minimum-area bounding rectangle of 2D points. Returns (width, depth, center, unit axis of width edge).
/// ponytail: O(hull²) edge scan, fine for a few thousand points; rotating calipers if it ever matters.
func minAreaRect(_ pts: [SIMD2<Float>]) -> (w: Float, d: Float, center: SIMD2<Float>, axis: SIMD2<Float>)? {
    let hull = convexHull(pts)
    guard hull.count >= 2 else { return nil }
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

/// Output of one scan — the only thing the packer needs per item. Dimensions in centimetres.
struct ScannedItem: Codable, Identifiable {
    var id = UUID()
    var width: Float
    var depth: Float
    var height: Float

    init(_ box: BoxFit) {
        width = box.width * 100; depth = box.depth * 100; height = box.height * 100
    }
}
