import Foundation
import simd

/// Coloured triangles from the actual ARKit mesh, for a filled/smooth 3D view -- the
/// counterpart to `VoxelGrid`, which stays the source of truth for noise-filtering,
/// connectivity (isolating the tapped object) and the measured bounding box.
///
/// Rationale: a point cloud (voxel centroids) always looks sparse and dotted no matter
/// how many points it has, because there is nothing filling the gaps between samples.
/// ARKit's mesh anchors already ARE a continuous triangulated surface; discarding that
/// and re-deriving points from it was throwing away exactly the thing that makes a
/// rendering look solid. This keeps the triangles and colours each vertex the same way
/// `FrameSampler` colours a voxel -- project it into a posed frame, sample the pixel --
/// so an object's visible faces end up textured, not just its measured extent.
///
/// Triangles are keyed by (mesh anchor, triangle index) rather than welded into a shared
/// vertex buffer: ARKit reissues the same anchor/triangle indexing as a scan refines, so
/// a later, better-lit frame simply overwrites an earlier colour sample for the same
/// triangle. Vertex welding would be more compact but is unnecessary for a scan of one
/// small object and meaningfully more code to get right under time pressure.
struct ColoredMesh {
    private struct Triangle {
        var vertices: (SIMD3<Float>, SIMD3<Float>, SIMD3<Float>)
        var colours: (SIMD3<Float>?, SIMD3<Float>?, SIMD3<Float>?)
    }
    private var triangles: [String: Triangle] = [:]

    var isEmpty: Bool { triangles.isEmpty }
    var count: Int { triangles.count }

    /// Record or refresh one triangle. `colour(at:)` is called once per vertex so a
    /// vertex outside the current frame (behind the camera, off-screen) just keeps
    /// whatever colour it already had, or none.
    mutating func add(anchorID: UUID, triangleIndex: Int, a: SIMD3<Float>, b: SIMD3<Float>, c: SIMD3<Float>,
                       colour: (SIMD3<Float>) -> SIMD3<Float>?) {
        let key = "\(anchorID)_\(triangleIndex)"
        var t = triangles[key] ?? Triangle(vertices: (a, b, c), colours: (nil, nil, nil))
        t.vertices = (a, b, c)
        if let ca = colour(a) { t.colours.0 = ca }
        if let cb = colour(b) { t.colours.1 = cb }
        if let cc = colour(c) { t.colours.2 = cc }
        triangles[key] = t
    }

    /// Triangles whose centroid falls in an occupied cell of `mask` -- the same
    /// pruned/connected voxel grid used for the measured box, so the mesh and the
    /// dimensions agree on what counts as "the object" rather than the table around it.
    func filtered(by mask: VoxelGrid) -> ColoredMesh {
        var out = ColoredMesh()
        for (key, t) in triangles {
            let centroid = (t.vertices.0 + t.vertices.1 + t.vertices.2) / 3
            if mask.cells[mask.key(for: centroid)] != nil { out.triangles[key] = t }
        }
        return out
    }
}

// MARK: - Serialisation

/// Wire form: every triangle stores its own three vertices (no shared vertex buffer),
/// which is simpler and plenty compact for one scanned object.
struct MeshPayload: Codable {
    /// x,y,z per vertex, 3 vertices per triangle.
    var vertices: [Float]
    /// 0..255 RGB per vertex, same order; 255 wherever a vertex was never coloured
    /// (kept visible as flat grey rather than invisible).
    var colours: [UInt8]
    var triangleCount: Int
}

extension ColoredMesh {
    func payload() -> MeshPayload {
        var vertices: [Float] = []; vertices.reserveCapacity(triangles.count * 9)
        var colours: [UInt8] = []; colours.reserveCapacity(triangles.count * 9)
        for t in triangles.values {
            for v in [t.vertices.0, t.vertices.1, t.vertices.2] { vertices += [v.x, v.y, v.z] }
            for c in [t.colours.0, t.colours.1, t.colours.2] {
                let rgb = (c ?? SIMD3(repeating: 0.6)).clamped(lowerBound: .zero, upperBound: .one) * 255
                colours += [UInt8(rgb.x.rounded()), UInt8(rgb.y.rounded()), UInt8(rgb.z.rounded())]
            }
        }
        return MeshPayload(vertices: vertices, colours: colours, triangleCount: triangles.count)
    }
}

private extension SIMD3 where Scalar == Float {
    static var one: SIMD3<Float> { SIMD3(repeating: 1) }
}
