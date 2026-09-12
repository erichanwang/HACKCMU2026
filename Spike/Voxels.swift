import Foundation
import simd

/// Sparse occupancy voxels with colour, accumulated across many viewpoints.
///
/// Why voxels rather than the single top-down heightmap this replaces: a heightmap is
/// 2.5D, so it can only ever record the surface facing the camera -- the underside of a
/// mug handle, the inside of a shoe and any overhang are structurally unrepresentable.
/// A voxel grid is true 3D, and because ARKit already reports every frame's pose in one
/// world frame, several passes around an object fuse by simply writing into the same
/// grid. There is no mesh stitching and no point-cloud registration step.
///
/// Deliberately free of ARKit and SwiftUI so every invariant below is testable on the
/// host with `tests/main.swift`. The ARKit glue (sampling camera pixels, walking mesh
/// anchors) lives in `ScanView.swift`.
///
/// Accuracy comes from repetition, not from a better single reading. LiDAR is roughly
/// +/-1 cm per sample, but the error is largely independent between frames and angles,
/// so a voxel seen from six viewpoints has a far better centroid than any one sample,
/// and a voxel seen exactly once is usually noise (see `pruned(minObservations:)`).

/// Integer lattice coordinate of a voxel. Int32 keeps the key small; at 5 mm voxels it
/// spans +/-10,000 km, so overflow is not a concern for anything on a table.
struct VoxelKey: Hashable {
    var x: Int32, y: Int32, z: Int32
}

/// Running totals for one voxel. Positions and colours are summed, never averaged in
/// place, so merging two grids is just addition and stays order-independent.
struct VoxelAccum {
    /// How many separate samples landed in this voxel. This is the confidence signal.
    var observations: UInt32 = 0
    /// Sum of sampled world positions, for a sub-voxel centroid.
    var positionSum: SIMD3<Float> = .zero
    /// Sum of sampled linear RGB in 0...1, over `colourSamples` samples.
    var colourSum: SIMD3<Float> = .zero
    /// Colour is optional per sample (a voxel can be behind the camera, or outside the
    /// frame), so it is counted separately from `observations`.
    var colourSamples: UInt32 = 0

    /// Sub-voxel centroid: the mean of what was actually measured, not the lattice centre.
    /// This is what makes the grid more precise than its own resolution.
    var centroid: SIMD3<Float> { positionSum / Float(max(observations, 1)) }

    var colour: SIMD3<Float>? {
        colourSamples == 0 ? nil : colourSum / Float(colourSamples)
    }
}

struct VoxelGrid {
    /// Edge length of one voxel, metres. 5 mm is near the useful floor for iPhone LiDAR:
    /// finer mostly buys noise, since a single sample is only good to about a centimetre.
    let voxelSize: Float
    private(set) var cells: [VoxelKey: VoxelAccum] = [:]

    init(voxelSize: Float = 0.005) {
        precondition(voxelSize > 0, "voxelSize must be positive")
        self.voxelSize = voxelSize
    }

    var count: Int { cells.count }
    var isEmpty: Bool { cells.isEmpty }

    func key(for p: SIMD3<Float>) -> VoxelKey {
        VoxelKey(x: Int32((p.x / voxelSize).rounded(.down)),
                 y: Int32((p.y / voxelSize).rounded(.down)),
                 z: Int32((p.z / voxelSize).rounded(.down)))
    }

    /// Record one observed surface point, optionally with the colour seen there.
    ///
    /// Hot path: this runs per sampled point, tens of thousands of times per second while
    /// scanning. The key is computed once and the dictionary slot is mutated in place, so
    /// there is one hash per call rather than the three a read-modify-write would cost.
    mutating func add(_ p: SIMD3<Float>, colour: SIMD3<Float>? = nil) {
        guard p.x.isFinite, p.y.isFinite, p.z.isFinite else { return }
        let k = key(for: p)
        var a = cells[k, default: VoxelAccum()]
        a.observations += 1
        a.positionSum += p
        if let c = colour {
            a.colourSum += c
            a.colourSamples += 1
        }
        cells[k] = a
    }

    /// Pre-size the table for an expected voxel count, so a long scan does not spend its
    /// time rehashing a growing dictionary mid-capture.
    mutating func reserve(_ n: Int) { cells.reserveCapacity(n) }

    /// Fold another pass into this one. Addition of sums, so passes commute.
    mutating func merge(_ other: VoxelGrid) {
        precondition(other.voxelSize == voxelSize, "grids must share a voxel size to merge")
        for (k, b) in other.cells {
            var a = cells[k] ?? VoxelAccum()
            a.observations += b.observations
            a.positionSum += b.positionSum
            a.colourSum += b.colourSum
            a.colourSamples += b.colourSamples
            cells[k] = a
        }
    }

    /// Drop voxels with too few observations. This is the noise filter: a stray LiDAR
    /// sample lands in a voxel once, while real surface is re-observed every frame that
    /// sees it. Raising the threshold trades coverage for cleanliness.
    func pruned(minObservations: UInt32 = 2) -> VoxelGrid {
        var out = VoxelGrid(voxelSize: voxelSize)
        out.cells = cells.filter { $0.value.observations >= minObservations }
        return out
    }

    /// The connected blob containing `seed`, by flood fill over the 26-neighbourhood.
    ///
    /// This is what separates the tapped object from whatever it is sitting on or next
    /// to, and it is strictly better than doing the same thing on a 2D footprint grid:
    /// in plan view a mug and the table under it overlap completely, so a 2D fill merges
    /// them, whereas in 3D they only connect if actual occupied voxels touch.
    /// 26-connectivity (faces, edges and corners) is deliberate -- scan data has holes,
    /// and 6-connectivity fragments a single object across them.
    func component(containing seed: SIMD3<Float>) -> VoxelGrid {
        var out = VoxelGrid(voxelSize: voxelSize)
        let start = nearestOccupied(to: seed)
        guard let start else { return out }

        var stack = [start], seen: Set<VoxelKey> = [start]
        while let k = stack.popLast() {
            out.cells[k] = cells[k]
            for dx in Int32(-1)...1 { for dy in Int32(-1)...1 { for dz in Int32(-1)...1 {
                if dx == 0 && dy == 0 && dz == 0 { continue }
                let n = VoxelKey(x: k.x + dx, y: k.y + dy, z: k.z + dz)
                if cells[n] != nil, seen.insert(n).inserted { stack.append(n) }
            } } }
        }
        return out
    }

    /// Occupied voxel nearest `p`, so a tap that lands in a gap still finds the object.
    /// Exact within one voxel, then a widening shell search; nil only if the grid is empty.
    func nearestOccupied(to p: SIMD3<Float>) -> VoxelKey? {
        let k = key(for: p)
        if cells[k] != nil { return k }
        guard !cells.isEmpty else { return nil }
        // Search outward a few shells before giving up and scanning everything, which
        // keeps the common "tap landed just off the surface" case cheap.
        for r in Int32(1)...4 {
            var best: VoxelKey?
            var bestD = Float.greatestFiniteMagnitude
            for dx in -r...r { for dy in -r...r { for dz in -r...r {
                guard max(abs(dx), max(abs(dy), abs(dz))) == r else { continue }  // shell only
                let n = VoxelKey(x: k.x + dx, y: k.y + dy, z: k.z + dz)
                guard let a = cells[n] else { continue }
                let d = simd_length_squared(a.centroid - p)
                if d < bestD { bestD = d; best = n }
            } } }
            if let best { return best }
        }
        return cells.min { simd_length_squared($0.value.centroid - p) < simd_length_squared($1.value.centroid - p) }?.key
    }

    /// Sub-voxel centroids of every occupied voxel.
    var points: [SIMD3<Float>] { cells.values.map(\.centroid) }

    /// Axis-aligned extent of the measured centroids (not of the lattice cells, which
    /// would over-report by up to one voxel in each direction).
    var bounds: (min: SIMD3<Float>, max: SIMD3<Float>)? {
        guard !cells.isEmpty else { return nil }
        var lo = SIMD3<Float>(repeating: .greatestFiniteMagnitude)
        var hi = SIMD3<Float>(repeating: -.greatestFiniteMagnitude)
        for a in cells.values {
            lo = simd_min(lo, a.centroid); hi = simd_max(hi, a.centroid)
        }
        return (lo, hi)
    }

    /// Total measured volume, counting each occupied voxel once. A surface scan only ever
    /// fills a shell, so this is a surface-area proxy, NOT the object's solid volume.
    var shellVolume: Float { Float(cells.count) * voxelSize * voxelSize * voxelSize }

    /// Fraction of voxels that carry at least one colour sample. Low values mean the
    /// geometry was captured but the camera never saw those faces lit.
    var colourCoverage: Float {
        guard !cells.isEmpty else { return 0 }
        return Float(cells.values.filter { $0.colourSamples > 0 }.count) / Float(cells.count)
    }
}

// MARK: - Serialisation

/// Wire form of a voxel grid: parallel arrays, which compress far better in JSON than an
/// array of objects and keep the document small enough for Mongo.
///
/// Coordinates are stored as lattice indices relative to `origin`, so they stay small
/// integers regardless of where in the ARKit world frame the object happened to be.
/// A 20 cm object at 5 mm is on the order of ten thousand surface voxels -- a few hundred
/// kilobytes -- comfortably inside Mongo's 16 MB document ceiling.
struct VoxelPayload: Codable {
    var voxelSize: Float
    /// World position of lattice index (0, 0, 0).
    var origin: [Float]
    /// Lattice indices, three per voxel: x0, y0, z0, x1, y1, z1, ...
    var indices: [Int32]
    /// 0...255 RGB, three per voxel, in the same order. Empty if nothing was coloured.
    var colours: [UInt8]
    /// Observation count per voxel, same order. Lets a consumer re-filter by confidence.
    var observations: [UInt32]
    /// Fraction of voxels that carried a colour sample, for a quality readout.
    var colourCoverage: Float
}

extension VoxelGrid {
    /// Pack for storage. Voxels are emitted in a deterministic order so the same scan
    /// serialises identically, which makes the payload diffable and testable.
    func payload() -> VoxelPayload {
        let ordered = cells.sorted {
            ($0.key.x, $0.key.y, $0.key.z) < ($1.key.x, $1.key.y, $1.key.z)
        }
        let base = ordered.first.map { SIMD3<Int32>($0.key.x, $0.key.y, $0.key.z) } ?? .zero

        var indices: [Int32] = []; indices.reserveCapacity(ordered.count * 3)
        var colours: [UInt8] = []; colours.reserveCapacity(ordered.count * 3)
        var obs: [UInt32] = []; obs.reserveCapacity(ordered.count)
        var anyColour = false

        for (k, a) in ordered {
            indices += [k.x - base.x, k.y - base.y, k.z - base.z]
            obs.append(a.observations)
            if let c = a.colour {
                anyColour = true
                colours += c.clamped(lowerBound: .zero, upperBound: .one)
                    .scaled(by: 255).rounded().map { UInt8($0) }
            } else {
                colours += [0, 0, 0]
            }
        }
        return VoxelPayload(
            voxelSize: voxelSize,
            origin: [Float(base.x) * voxelSize, Float(base.y) * voxelSize, Float(base.z) * voxelSize],
            indices: indices,
            colours: anyColour ? colours : [],
            observations: obs,
            colourCoverage: colourCoverage
        )
    }
}

private extension SIMD3 where Scalar == Float {
    static var one: SIMD3<Float> { SIMD3(repeating: 1) }
    func scaled(by s: Float) -> SIMD3<Float> { self * s }
    func rounded() -> [Float] { [x.rounded(), y.rounded(), z.rounded()] }
}

// MARK: - Capture guidance

/// Tracks which directions an object has been seen from, so the app can tell the user
/// where to move instead of leaving them to guess when a scan is "done".
///
/// Progress is measured on the AZIMUTH RING alone -- the circle of directions you sweep by
/// walking once around an object. That is deliberate: the natural gesture is a single
/// orbit, and it should read as a finished scan. An earlier version also binned elevation,
/// so one lap round a table topped out at a third of the bar and the scan looked broken
/// when it was in fact complete.
///
/// A high view is tracked separately as `sawTop`, because an object's upper faces are
/// genuinely invisible from a horizontal orbit. It is reported as a suggestion once the
/// ring is closed, not as missing progress.
///
/// The underside is never modelled; an object resting on a table cannot be scanned from
/// beneath it.
struct ViewCoverage {
    let azimuthBins: Int
    /// Above this elevation a view counts as looking down on the object.
    let topElevationRadians: Float
    private(set) var seen: Set<Int> = []
    private(set) var sawTop = false

    init(azimuthBins: Int = 12, topElevationRadians: Float = .pi / 4) {
        precondition(azimuthBins > 0)
        self.azimuthBins = azimuthBins
        self.topElevationRadians = topElevationRadians
    }

    var total: Int { azimuthBins }
    var covered: Int { seen.count }
    /// 0...1 around the ring. Reaches 1 after one complete orbit.
    var fraction: Float { Float(covered) / Float(total) }
    /// Everything worth capturing: the full ring plus at least one look from above.
    var isComplete: Bool { covered == total && sawTop }

    private func normalised(_ direction: SIMD3<Float>) -> SIMD3<Float> {
        simd_length(direction) > 1e-6 ? simd_normalize(direction) : SIMD3<Float>(0, 1, 0)
    }

    /// Which slice of the ring a direction falls in. Nil is never returned; the optional
    /// is kept so callers can stay tolerant of a degenerate direction.
    func bin(for direction: SIMD3<Float>) -> Int? {
        let d = normalised(direction)
        var azimuth = atan2(d.z, d.x)
        if azimuth < 0 { azimuth += 2 * .pi }
        return min(azimuthBins - 1, Int(azimuth / (2 * .pi) * Float(azimuthBins)))
    }

    func isTopView(_ direction: SIMD3<Float>) -> Bool {
        asin(max(-1, min(1, normalised(direction).y))) >= topElevationRadians
    }

    /// Record that the object was observed from `direction` (object -> camera).
    mutating func record(direction: SIMD3<Float>) {
        if let b = bin(for: direction) { seen.insert(b) }
        if isTopView(direction) { sawTop = true }
    }

    /// Human instruction for what is still missing, given where the camera is now.
    /// Returns nil once the ring is closed and a top view has been captured.
    func nextHint(from direction: SIMD3<Float>) -> String? {
        guard let current = bin(for: direction) else { return nil }
        if covered < total {
            // Nearest unseen slice, by shortest way round the circle.
            func signedDelta(_ target: Int) -> Int {
                var d = target - current
                if d > azimuthBins / 2 { d -= azimuthBins }
                if d < -azimuthBins / 2 { d += azimuthBins }
                return d
            }
            guard let target = (0..<total).filter({ !seen.contains($0) })
                .min(by: { abs(signedDelta($0)) < abs(signedDelta($1)) }) else { return nil }
            return signedDelta(target) > 0 ? "Keep moving left around the object"
                                           : "Keep moving right around the object"
        }
        return sawTop ? nil : "Now hold the phone above it and look down"
    }
}
