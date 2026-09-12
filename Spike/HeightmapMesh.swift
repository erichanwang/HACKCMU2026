import PackingPlan
import RealityKit

/// Turns a `ScannedItem`'s heightmap into a RealityKit mesh, so a packed item is
/// drawn as the shape the LiDAR actually saw instead of its bounding box.
///
/// The grid is `heights[i][j]`: `i` along the item's width (local X), `j` along
/// its depth (local Z), each value the surface height above the base (local Y).
/// Zero means nothing is there. The solid is the volume under the heightmap, so
/// each non-zero cell becomes a column: a quad on top at its own height, a quad
/// on the base at y = 0, and walls wherever it is taller than what is beside it.
///
/// Walls are emitted on three occasions, not two:
/// - against an **empty** neighbour, dropping the full height to y = 0;
/// - around the grid **perimeter**, likewise to y = 0;
/// - against a **shorter filled** neighbour, dropping only the step between them.
///
/// That third case is not decorative. Without it, two neighbouring columns of
/// different heights leave a vertical slot between their top quads and the solid
/// is not closed — you would see straight through the side of the object.
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
        descriptor.textureCoordinates = MeshBuffers.TextureCoordinates(surface.uvs)
        descriptor.primitives = .triangles(Array(UInt32(0)..<UInt32(surface.positions.count)))
        return try? MeshResource.generate(from: [descriptor])
    }

    /// The mesh as flat-shaded triangle soup, with no RealityKit involved so the
    /// geometry can be checked on its own.
    ///
    /// `place` maps a vertex from local scan space into the placement's box.
    static func surface(
        heights grid: [[Float]],
        cellSize cell: Float,
        place: (SIMD3<Float>) -> SIMD3<Float>
    ) -> (positions: [SIMD3<Float>], normals: [SIMD3<Float>], uvs: [SIMD2<Float>])? {
        guard cell > 0,
              let firstRow = grid.first,
              !firstRow.isEmpty,
              grid.allSatisfy({ $0.count == firstRow.count })
        else { return nil }

        let rows = grid.count
        let cols = firstRow.count
        // Matches `ScanView.Coordinator.bakeColorMap`'s pixel layout: image width = rows (the
        // i/width axis), height = cols (the j/depth axis). Walls and the base reuse the top's
        // (x, z) coordinate at their own corner, so they read as the top colour smeared straight
        // down — there's no captured colour for a side, and that's a better guess than a flat tint.
        let spanI = Float(rows) * cell, spanJ = Float(cols) * cell

        var positions: [SIMD3<Float>] = []
        var normals: [SIMD3<Float>] = []
        var uvs: [SIMD2<Float>] = []

        /// Flat-shaded: each triangle carries its own three vertices and one
        /// normal, so a wall never smears into the top it meets.
        func triangle(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>) {
            let pa = place(a), pb = place(b), pc = place(c)
            let normal = simd_cross(pb - pa, pc - pa)
            guard simd_length(normal) > 0 else { return }
            let unit = simd_normalize(normal)
            positions += [pa, pb, pc]
            normals += [unit, unit, unit]
            uvs += [SIMD2(a.x / spanI, a.z / spanJ), SIMD2(b.x / spanI, b.z / spanJ), SIMD2(c.x / spanI, c.z / spanJ)]
        }

        func quad(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>, _ d: SIMD3<Float>) {
            triangle(a, b, c)
            triangle(a, c, d)
        }

        /// Height beside cell (i, j); 0 off the grid or on an empty cell, which is
        /// what makes the perimeter and the filled/empty boundary the same case.
        func neighbour(_ i: Int, _ j: Int) -> Float {
            guard i >= 0, i < rows, j >= 0, j < cols else { return 0 }
            return max(0, grid[i][j])
        }

        var drawn = 0
        for i in 0..<rows {
            for j in 0..<cols {
                let top = grid[i][j]
                guard top > 0 else { continue }
                drawn += 1

                let x0 = Float(i) * cell, x1 = x0 + cell
                let z0 = Float(j) * cell, z1 = z0 + cell

                // Top face, wound counter-clockwise seen from above (+Y).
                quad(
                    SIMD3(x0, top, z0), SIMD3(x0, top, z1),
                    SIMD3(x1, top, z1), SIMD3(x1, top, z0)
                )
                // Base face at y = 0, facing down.
                quad(
                    SIMD3(x1, 0, z0), SIMD3(x1, 0, z1),
                    SIMD3(x0, 0, z1), SIMD3(x0, 0, z0)
                )

                // +X wall.
                let east = neighbour(i + 1, j)
                if top > east {
                    quad(
                        SIMD3(x1, top, z0), SIMD3(x1, top, z1),
                        SIMD3(x1, east, z1), SIMD3(x1, east, z0)
                    )
                }
                // −X wall.
                let west = neighbour(i - 1, j)
                if top > west {
                    quad(
                        SIMD3(x0, top, z1), SIMD3(x0, top, z0),
                        SIMD3(x0, west, z0), SIMD3(x0, west, z1)
                    )
                }
                // +Z wall.
                let south = neighbour(i, j + 1)
                if top > south {
                    quad(
                        SIMD3(x1, top, z1), SIMD3(x0, top, z1),
                        SIMD3(x0, south, z1), SIMD3(x1, south, z1)
                    )
                }
                // −Z wall.
                let north = neighbour(i, j - 1)
                if top > north {
                    quad(
                        SIMD3(x0, top, z0), SIMD3(x1, top, z0),
                        SIMD3(x1, north, z0), SIMD3(x0, north, z0)
                    )
                }
            }
        }

        guard drawn > 0 else { return nil }
        return (positions, normals, uvs)
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
