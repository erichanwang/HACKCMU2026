import Foundation

/// Where the eye is for the 3D plan view.
///
/// The projection is **orthographic**. A suitcase is a shoebox seen from arm's
/// length, so a perspective divide buys a pixel of foreshortening and costs a
/// depth-correct painter's sort. Angles are radians.
public struct PlanCamera: Hashable, Sendable {
    /// Rotation about bag **+Y** (up). `0` looks along −Z, straight at the bag's
    /// far wall, with bag +X to the right.
    public var yaw: Float

    /// Elevation above the horizon, clamped to ±1.5 rad. At a true 90° the
    /// view is straight down: the top face fills the screen, the sides vanish and
    /// the bag stops reading as a solid.
    public var pitch: Float

    /// Magnification. `1` fits the whole container in the size handed to
    /// `projected(camera:size:upTo:)`; clamped to stay positive.
    public var zoom: Float

    public init(yaw: Float, pitch: Float, zoom: Float) {
        self.yaw = yaw
        self.pitch = min(max(pitch, -Self.pitchLimit), Self.pitchLimit)
        self.zoom = max(zoom, Self.minimumZoom)
    }

    static let pitchLimit: Float = 1.5
    static let minimumZoom: Float = 1e-3

    /// Over the right shoulder and above — the three-quarter view that shows a
    /// top and two sides, so every box reads as 3D without being turned.
    public static let `default` = PlanCamera(yaw: 0.6, pitch: 0.5, zoom: 1)
}

/// One quad of a box that the camera can actually see.
public struct ProjectedFace: Hashable, Sendable {
    /// Four screen-space points in ring order, **y increasing downward**.
    public let corners: [SIMD2<Float>]

    /// Lighting factor for the fill, `0...1`, `1` brightest. Derived from the
    /// face normal against a fixed bag-space key light, so a face keeps its
    /// shade as the camera spins and the three visible faces stay distinct.
    public let shade: Float
}

/// A plan box flattened into the view: fills, a silhouette, a label anchor and a
/// sort key. Everything a renderer needs and nothing it has to recompute.
public struct ProjectedBox: Identifiable, Hashable, Sendable {
    /// `Placement.itemID`, or `"container"` for the bag itself.
    public let id: String

    /// `Placement.step`; `0` for the container.
    public let step: Int

    public let label: String

    /// Visible faces only — at most three, back faces culled.
    public let faces: [ProjectedFace]

    /// Silhouette polygon (the convex hull of the eight projected corners), for
    /// a crisp edge stroke that does not draw the seams between the fills.
    public let outline: [SIMD2<Float>]

    /// Screen-space centre of the box, for the label.
    public let center: SIMD2<Float>

    /// View-space depth of the box centre in metres, larger = farther.
    ///
    /// A readout and a tie-break, **not** the draw order: no single scalar orders
    /// a stack correctly, because a wide flat box can have a nearer centre than
    /// the small box standing on it. The array order is the contract.
    public let sortDepth: Float
}

public extension PackingPlan {
    /// Placements projected into a `size`-sized rect, ordered back to front
    /// (draw in array order).
    ///
    /// The container is always element 0: it is rendered from the inside — the
    /// walls facing *away* from the camera, which is what you see looking into an
    /// open bag — so it never paints over the items. `upTo` keeps only placements
    /// with `step <= upTo`.
    func projected(camera: PlanCamera, size: SIMD2<Float>, upTo limit: Int?) -> [ProjectedBox] {
        let projector = Projector(camera: camera, interior: container.interior, size: size)

        let bag = projector.box(
            container.interior,
            id: "container",
            step: 0,
            label: container.label,
            seenFromInside: true
        )

        let items = orderedPlacements.filter { $0.step <= (limit ?? Int.max) }.map { placement in
            (
                solid: placement.box,
                projected: projector.box(
                    placement.box,
                    id: placement.itemID,
                    step: placement.step,
                    label: placement.label,
                    seenFromInside: false
                )
            )
        }

        return [bag] + painterOrdered(items, toCamera: projector.basis.toCamera)
    }
}

// MARK: - Painter's order

/// Back-to-front order for boxes that do not interpenetrate.
///
/// A single depth scalar cannot do this: a wide flat box can have a nearer centre
/// than the small box resting on it, and then its top face gets painted back over
/// the thing standing on it — the second layer of a packed bag, in the default
/// view. So the order comes from the pairwise relation instead: for every pair
/// whose silhouettes overlap, work out which one is in front, then topologically
/// sort. Boxes that cannot occlude each other stay in centre-depth order, which
/// is also the fallback if the relation ever comes out cyclic.
private func painterOrdered(
    _ items: [(solid: BoundingBox, projected: ProjectedBox)],
    toCamera: SIMD3<Float>
) -> [ProjectedBox] {
    // Steps are unique, so this order is total: a flat-on view of a stack still
    // comes out the same way every run.
    var remaining = items.indices.sorted {
        let (a, b) = (items[$0].projected, items[$1].projected)
        return a.sortDepth == b.sortDepth ? a.step < b.step : a.sortDepth > b.sortDepth
    }

    var inFront: [[Int]] = Array(repeating: [], count: items.count)
    var behindCount = [Int](repeating: 0, count: items.count)
    for i in items.indices {
        for j in items.indices where j > i {
            guard silhouettesOverlap(items[i].projected.outline, items[j].projected.outline),
                  let near = nearer(items[i].solid, items[j].solid, toCamera: toCamera)
            else { continue }
            let (front, back) = near == 0 ? (i, j) : (j, i)
            inFront[back].append(front)
            behindCount[front] += 1
        }
    }

    var order: [ProjectedBox] = []
    while !remaining.isEmpty {
        guard let next = remaining.firstIndex(where: { behindCount[$0] == 0 }) else {
            // Three or more boxes each in front of the next: no draw order is
            // right, so emit the rest farthest-centre-first rather than drop one.
            return order + remaining.map { items[$0].projected }
        }
        let node = remaining.remove(at: next)
        order.append(items[node].projected)
        for front in inFront[node] { behindCount[front] -= 1 }
    }
    return order
}

/// Which of two boxes is nearer the camera — `0` for `a`, `1` for `b` — or `nil`
/// if they interpenetrate and neither is.
///
/// Their extents along the view direction settle it outright when they are
/// disjoint. When those overlap (two items side by side reach the same depths),
/// the bag axis that actually separates them does, taking the most view-aligned
/// such axis: that is the separation the eye reads as depth.
private func nearer(_ a: BoundingBox, _ b: BoundingBox, toCamera: SIMD3<Float>) -> Int? {
    let (aLow, aHigh) = depthSpan(a, toCamera)
    let (bLow, bHigh) = depthSpan(b, toCamera)
    if aHigh <= bLow { return 1 }
    if bHigh <= aLow { return 0 }

    var best: (axis: Axis, pull: Float)?
    for axis in Axis.allCases {
        let separated = a.maxCorner[axis] <= b.minCorner[axis] + 1e-6
            || b.maxCorner[axis] <= a.minCorner[axis] + 1e-6
        let pull = abs(toCamera[axis.rawValue])
        guard separated, pull > (best?.pull ?? 0) else { continue }
        best = (axis, pull)
    }
    guard let (axis, _) = best else { return nil }

    let aIsLow = a.maxCorner[axis] <= b.minCorner[axis] + 1e-6
    let lowIsNearer = toCamera[axis.rawValue] < 0
    return aIsLow == lowIsNearer ? 0 : 1
}

private func depthSpan(_ box: BoundingBox, _ toCamera: SIMD3<Float>) -> (Float, Float) {
    let dots = box.corners.map { dot($0, toCamera) }
    return (dots.min() ?? 0, dots.max() ?? 0)
}

/// Separating-axis test on the two silhouettes, which are convex.
///
/// Worth doing exactly rather than on screen bounds: a bounds hit between two
/// boxes that do not actually overlap still adds an ordering constraint, and
/// enough of those contradict each other and tip the sort into its fallback.
private func silhouettesOverlap(_ a: [SIMD2<Float>], _ b: [SIMD2<Float>]) -> Bool {
    guard a.count > 2, b.count > 2 else { return false }
    for polygon in [a, b] {
        for i in polygon.indices {
            let edge = polygon[(i + 1) % polygon.count] - polygon[i]
            let axis = SIMD2(-edge.y, edge.x)
            let (aLow, aHigh) = span(a, axis)
            let (bLow, bHigh) = span(b, axis)
            if aHigh <= bLow || bHigh <= aLow { return false }
        }
    }
    return true
}

private func span(_ polygon: [SIMD2<Float>], _ axis: SIMD2<Float>) -> (Float, Float) {
    let dots = polygon.map { $0.x * axis.x + $0.y * axis.y }
    return (dots.min() ?? 0, dots.max() ?? 0)
}

// MARK: - Internals

private func dot(_ a: SIMD3<Float>, _ b: SIMD3<Float>) -> Float {
    a.x * b.x + a.y * b.y + a.z * b.z
}

/// Axis-aligned bounds of a point set. Empty comes back inverted, so every
/// overlap test against it is false rather than a crash.
private func bounds(_ points: [SIMD2<Float>]) -> (low: SIMD2<Float>, high: SIMD2<Float>) {
    var low = SIMD2<Float>(repeating: .greatestFiniteMagnitude)
    var high = -low
    for p in points {
        low = SIMD2(min(low.x, p.x), min(low.y, p.y))
        high = SIMD2(max(high.x, p.x), max(high.y, p.y))
    }
    return (low, high)
}

private func normalized(_ v: SIMD3<Float>) -> SIMD3<Float> {
    let length = dot(v, v).squareRoot()
    return length > 0 ? v / length : v
}

/// Fixed in bag space, not camera space: shading that swings with the camera
/// makes a static plan look like it is rippling while the user drags.
private let keyLight = normalized(SIMD3<Float>(0.4, 1, 0.6))

/// An orthonormal view frame: screen right, screen up, and the direction from
/// the scene *toward* the camera. Built yaw-then-pitch about bag +Y.
private struct ViewBasis {
    let right: SIMD3<Float>
    let up: SIMD3<Float>
    let toCamera: SIMD3<Float>

    init(_ camera: PlanCamera) {
        let (sy, cy) = (sin(camera.yaw), cos(camera.yaw))
        let (sp, cp) = (sin(camera.pitch), cos(camera.pitch))
        right = SIMD3(cy, 0, -sy)
        up = SIMD3(-sy * sp, cp, -cy * sp)
        toCamera = SIMD3(sy * cp, sp, cy * cp)
    }

    /// View-plane coordinates, y already flipped to point down the screen.
    func flatten(_ p: SIMD3<Float>) -> SIMD2<Float> {
        SIMD2(dot(p, right), -dot(p, up))
    }
}

/// The view basis plus the scale-to-fit that lands the container in the rect.
private struct Projector {
    /// Fraction of the rect the container spans at zoom 1; the rest is margin.
    static let fitFraction: Float = 0.92

    let basis: ViewBasis
    let scale: Float
    let offset: SIMD2<Float>

    init(camera: PlanCamera, interior: BoundingBox, size: SIMD2<Float>) {
        let basis = ViewBasis(camera)
        self.basis = basis

        let (low, high) = bounds(interior.corners.map(basis.flatten))

        // A degenerate container or a zero-sized rect collapses to scale 0 —
        // everything lands on the centre point rather than on a NaN.
        let extent = high - low
        let fit = min(
            extent.x > 0 ? size.x / extent.x : .infinity,
            extent.y > 0 ? size.y / extent.y : .infinity
        )
        scale = fit.isFinite ? fit * Self.fitFraction * camera.zoom : 0
        offset = size / 2 - (low + high) / 2 * scale
    }

    func screen(_ p: SIMD3<Float>) -> SIMD2<Float> {
        basis.flatten(p) * scale + offset
    }

    /// `seenFromInside` flips every face normal, which both keeps the container's
    /// far walls (and drops its near ones) and lights those walls from the cavity
    /// side, so the floor reads as lit from above rather than as an underside.
    func box(
        _ box: BoundingBox,
        id: String,
        step: Int,
        label: String,
        seenFromInside: Bool
    ) -> ProjectedBox {
        let facing: Float = seenFromInside ? -1 : 1
        var faces: [ProjectedFace] = []
        for axis in 0..<3 {
            for high in [false, true] {
                var normal = SIMD3<Float>()
                normal[axis] = (high ? 1 : -1) * facing
                guard dot(normal, basis.toCamera) > 0 else { continue }
                faces.append(
                    ProjectedFace(
                        corners: box.faceCorners(axis: axis, high: high).map(screen),
                        shade: 0.35 + 0.65 * max(0, dot(normal, keyLight))
                    )
                )
            }
        }
        return ProjectedBox(
            id: id,
            step: step,
            label: label,
            faces: faces,
            outline: convexHull(box.corners.map(screen)),
            center: screen(box.center.simd),
            sortDepth: -dot(box.center.simd, basis.toCamera)
        )
    }
}

private extension BoundingBox {
    var corners: [SIMD3<Float>] {
        let lo = minCorner.simd
        let hi = maxCorner.simd
        return (0..<8).map { i in
            SIMD3(
                i & 1 == 0 ? lo.x : hi.x,
                i & 2 == 0 ? lo.y : hi.y,
                i & 4 == 0 ? lo.z : hi.z
            )
        }
    }

    /// The four corners of one face, walked as a ring about `axis`.
    func faceCorners(axis: Int, high: Bool) -> [SIMD3<Float>] {
        let lo = minCorner.simd
        let hi = maxCorner.simd
        let (u, v) = ((axis + 1) % 3, (axis + 2) % 3)
        return [(false, false), (true, false), (true, true), (false, true)].map { pick in
            var p = SIMD3<Float>()
            p[axis] = high ? hi[axis] : lo[axis]
            p[u] = pick.0 ? hi[u] : lo[u]
            p[v] = pick.1 ? hi[v] : lo[v]
            return p
        }
    }
}

/// Monotone-chain hull of the eight projected corners: a hexagon in general, and
/// a segment or a point when the box or the scale degenerates — never a
/// self-crossing outline.
private func convexHull(_ points: [SIMD2<Float>]) -> [SIMD2<Float>] {
    let sorted = points.sorted { $0.x == $1.x ? $0.y < $1.y : $0.x < $1.x }
    guard sorted.count > 2 else { return sorted }

    func turn(_ o: SIMD2<Float>, _ a: SIMD2<Float>, _ b: SIMD2<Float>) -> Float {
        (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x)
    }

    func chain(_ points: [SIMD2<Float>]) -> [SIMD2<Float>] {
        var hull: [SIMD2<Float>] = []
        for p in points {
            while hull.count >= 2, turn(hull[hull.count - 2], hull[hull.count - 1], p) <= 0 {
                hull.removeLast()
            }
            hull.append(p)
        }
        hull.removeLast()
        return hull
    }

    return chain(sorted) + chain(sorted.reversed())
}
