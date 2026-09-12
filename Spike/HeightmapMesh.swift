import PackingPlan
import RealityKit

/// Turns a `ScannedItem`'s heightmap into a RealityKit mesh, so a packed item is
/// drawn as the shape the LiDAR actually saw instead of its bounding box.
///
/// The grid is `heights[i][j]`, `i` along the item's width (local X), `j` along
/// its depth (local Z), each value the surface height above the table (local Y).
/// Zero means nothing was there — see SCAN_OUTPUT.md.
enum HeightmapMesh {

    /// Builds a mesh for `item`, expressed in `placement`'s box.
    ///
    /// Returns `nil` when the scan carries nothing renderable — an empty or
    /// ragged grid, a bad cell size, or a grid that is all zeroes — so the caller
    /// can fall back to a plain box.
    ///
    /// The returned mesh is authored from its **min corner**, not its centre:
    /// unlike `MeshResource.generateBox`, an entity using it wants
    /// `placement.position`, not `placement.renderCenter`.
    static func generate(from item: ScannedItem, fitting placement: Placement) -> MeshResource? {
        guard let surface = surface(
            heights: item.heights,
            cellSize: item.cellSize,
            place: placer(
                for: placement,
                localExtent: SIMD3(
                    Float(item.heights.count) * item.cellSize,
                    item.heights.flatMap { $0 }.max() ?? item.cellSize,
                    Float(item.heights.first?.count ?? 0) * item.cellSize
                )
            )
        ) else { return nil }

        var descriptor = MeshDescriptor(name: "heightmap")
        descriptor.positions = MeshBuffers.Positions(surface.positions)
        descriptor.normals = MeshBuffers.Normals(surface.normals)
        descriptor.primitives = .triangles(Array(UInt32(0)..<UInt32(surface.positions.count)))
        return try? MeshResource.generate(from: [descriptor])
    }

    /// The mesh as flat-shaded triangle soup, with no RealityKit involved so the
    /// geometry can be checked on its own.
    ///
    /// `place` maps a vertex from local scan space into the placement's box.
    static func surface(
        heights grid: [[Float]],
        cellSize: Float,
        place: (SIMD3<Float>) -> SIMD3<Float>
    ) -> (positions: [SIMD3<Float>], normals: [SIMD3<Float>])? {
        guard cellSize > 0,
              let firstRow = grid.first,
              !firstRow.isEmpty,
              grid.allSatisfy({ $0.count == firstRow.count })
        else { return nil }

        let rows = grid.count
        let cols = firstRow.count
        let cell = cellSize

        // One vertex per filled cell, at the cell's centre and its own height.
        var vertexIndex = Array(repeating: Array(repeating: -1, count: cols), count: rows)
        var top: [SIMD3<Float>] = []
        for i in 0..<rows {
            for j in 0..<cols where grid[i][j] > 0 {
                vertexIndex[i][j] = top.count
                top.append(SIMD3(
                    (Float(i) + 0.5) * cell,
                    grid[i][j],
                    (Float(j) + 0.5) * cell
                ))
            }
        }
        guard top.count >= 3 else { return nil }

        let surface = triangulate(vertexIndex: vertexIndex, rows: rows, cols: cols)
        guard !surface.isEmpty else { return nil }

        var positions: [SIMD3<Float>] = []
        var normals: [SIMD3<Float>] = []

        /// Flat-shaded: each triangle gets its own three vertices and one normal,
        /// which keeps the skirt from smearing into the top surface.
        func emit(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>) {
            let pa = place(a), pb = place(b), pc = place(c)
            let normal = simd_cross(pb - pa, pc - pa)
            guard simd_length(normal) > 0 else { return }
            let unit = simd_normalize(normal)
            positions += [pa, pb, pc]
            normals += [unit, unit, unit]
        }

        // Top surface.
        for triangle in surface {
            emit(top[triangle.0], top[triangle.1], top[triangle.2])
        }

        // Base cap: the same triangles flattened to y = 0, wound the other way.
        func floored(_ v: SIMD3<Float>) -> SIMD3<Float> { SIMD3(v.x, 0, v.z) }
        for triangle in surface {
            emit(floored(top[triangle.2]), floored(top[triangle.1]), floored(top[triangle.0]))
        }

        // Skirt walls: every edge used by exactly one triangle is a silhouette
        // edge — the rim of the object or the lip of a hole — so drop a wall from
        // it to the base and the solid closes up.
        for (a, b) in boundaryEdges(of: surface) {
            emit(top[a], floored(top[b]), top[b])
            emit(top[a], floored(top[a]), floored(top[b]))
        }

        return (positions, normals)
    }

    // MARK: - Topology

    private typealias Triangle = (Int, Int, Int)

    /// Triangulates the filled cells, walking each 2×2 block of the grid.
    ///
    /// A block with all four corners filled becomes two triangles; three filled
    /// becomes one, which is what lets a diagonal edge stay diagonal instead of
    /// going blocky. Every triangle is wound counter-clockwise seen from above,
    /// so its normal points along +Y.
    private static func triangulate(
        vertexIndex: [[Int]],
        rows: Int,
        cols: Int
    ) -> [Triangle] {
        var triangles: [Triangle] = []
        guard rows > 1, cols > 1 else { return triangles }

        for i in 0..<(rows - 1) {
            for j in 0..<(cols - 1) {
                let v00 = vertexIndex[i][j]
                let v10 = vertexIndex[i + 1][j]
                let v01 = vertexIndex[i][j + 1]
                let v11 = vertexIndex[i + 1][j + 1]

                switch (v00 >= 0, v10 >= 0, v01 >= 0, v11 >= 0) {
                case (true, true, true, true):
                    triangles.append((v00, v01, v10))
                    triangles.append((v10, v01, v11))
                case (true, true, true, false):
                    triangles.append((v00, v01, v10))
                case (true, true, false, true):
                    triangles.append((v00, v11, v10))
                case (true, false, true, true):
                    triangles.append((v00, v01, v11))
                case (false, true, true, true):
                    triangles.append((v10, v01, v11))
                default:
                    break  // two or fewer corners: nothing to draw
                }
            }
        }
        return triangles
    }

    /// Edges belonging to exactly one triangle, returned in that triangle's own
    /// winding so a wall hung from them faces outward.
    private static func boundaryEdges(of triangles: [Triangle]) -> [(Int, Int)] {
        struct Edge: Hashable { let low: Int, high: Int }

        var uses: [Edge: Int] = [:]
        var firstDirection: [Edge: (Int, Int)] = [:]

        for triangle in triangles {
            for (u, v) in [
                (triangle.0, triangle.1),
                (triangle.1, triangle.2),
                (triangle.2, triangle.0),
            ] {
                let edge = Edge(low: min(u, v), high: max(u, v))
                uses[edge, default: 0] += 1
                if firstDirection[edge] == nil { firstDirection[edge] = (u, v) }
            }
        }

        return uses.compactMap { edge, count in
            count == 1 ? firstDirection[edge] : nil
        }
    }

    // MARK: - Placing the scan inside the packed box

    /// Local scan space → the placement's box, as a single closure.
    ///
    /// The scan's own axes are width/up/depth; `placement.rotation` says which of
    /// the item's axes the solver laid along each bag axis. Whatever the packed
    /// size ends up being, the mesh is stretched to fill it exactly, so a scan
    /// whose padding disagrees with the solver by a millimetre cannot poke out of
    /// the container.
    private static func placer(
        for placement: Placement,
        localExtent: SIMD3<Float>
    ) -> (SIMD3<Float>) -> SIMD3<Float> {
        let target = placement.size.simd
        var sourceAxis = SIMD3<Int32>(0, 1, 2)
        var scale = SIMD3<Float>(repeating: 1)

        for bagAxis in Axis.allCases {
            let local = placement.rotation.localAxis(forBagAxis: bagAxis)
            sourceAxis[bagAxis.rawValue] = Int32(local.rawValue)
            let extent = localExtent[local.rawValue]
            scale[bagAxis.rawValue] = extent > 0 ? target[bagAxis.rawValue] / extent : 1
        }

        return { local in
            SIMD3(
                local[Int(sourceAxis[0])] * scale[0],
                local[Int(sourceAxis[1])] * scale[1],
                local[Int(sourceAxis[2])] * scale[2]
            )
        }
    }
}
