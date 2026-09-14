import PackingPlan
import RealityKit

/// Turns a `ScannedItem`'s heightmap into a RealityKit mesh, so a packed item is
/// drawn as the shape the LiDAR actually saw instead of its bounding box.
///
/// The grid is `heights[i][j]`: `i` along the item's width (local X), `j` along
/// its depth (local Z), each value the surface height above the base (local Y).
/// Zero means nothing is there. The solid is the volume under the heightmap.
///
/// The top is **not** one flat plateau per cell — that reads as a staircase of
/// Lego-like blocks, because every internal height change becomes a vertical
/// cliff. Instead each cell's four corners are the *average* of the (up to four)
/// cells that meet there (`corner(_:_:)`), so adjacent cells already agree on
/// their shared corner height and the surface between them tilts smoothly
/// instead of stepping. A corner next to an empty cell or the grid edge is
/// pulled toward 0 by that neighbour rather than dropping straight down, which
/// is also why only the true silhouette — a cell actually bordering empty space
/// or the perimeter — still gets a vertical skirt wall down to y = 0; two filled
/// neighbours never do, because the smoothed corners already meet there.
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

        /// Cell height, 0 off the grid or on an empty cell — the implicit "outside"
        /// value that pulls a corner's average down near the object's edge.
        func cellHeight(_ i: Int, _ j: Int) -> Float {
            guard i >= 0, i < rows, j >= 0, j < cols else { return 0 }
            return max(0, grid[i][j])
        }
        func occupied(_ i: Int, _ j: Int) -> Bool { cellHeight(i, j) > 0 }

        /// Height at grid corner (i, j), i in 0...rows, j in 0...cols: the average
        /// of whichever of the (up to four) cells meeting there are filled, empty
        /// and off-grid ones excluded rather than counted as 0. Neighbouring
        /// cells share this corner vertex and so agree on its height by
        /// construction — that shared agreement is what turns a staircase of flat
        /// plateaus into one continuous, gently sloped surface. Excluding empty
        /// neighbours (instead of averaging them in as 0) matters at the object's
        /// true edge: a flat-topped object's outer corners still average only
        /// their one or two real neighbours and so stay at the real height,
        /// dropping to y = 0 in one clean skirt wall — counting the empty side
        /// too would melt every edge into a rounded taper that was never scanned.
        func corner(_ i: Int, _ j: Int) -> Float {
            var sum: Float = 0, count: Float = 0
            for di in [-1, 0] {
                for dj in [-1, 0] {
                    let h = cellHeight(i + di, j + dj)
                    if h > 0 { sum += h; count += 1 }
                }
            }
            return count > 0 ? sum / count : 0
        }

        var drawn = 0
        for i in 0..<rows {
            for j in 0..<cols {
                guard grid[i][j] > 0 else { continue }
                drawn += 1

                let x0 = Float(i) * cell, x1 = x0 + cell
                let z0 = Float(j) * cell, z1 = z0 + cell
                // This cell's four corners, shared with whichever neighbours are
                // also filled — the top tilts through these, it is not flat.
                let h00 = corner(i, j), h01 = corner(i, j + 1)
                let h11 = corner(i + 1, j + 1), h10 = corner(i + 1, j)

                // Top face, wound counter-clockwise seen from above (+Y).
                quad(
                    SIMD3(x0, h00, z0), SIMD3(x0, h01, z1),
                    SIMD3(x1, h11, z1), SIMD3(x1, h10, z0)
                )
                // Base face at y = 0, facing down.
                quad(
                    SIMD3(x1, 0, z0), SIMD3(x1, 0, z1),
                    SIMD3(x0, 0, z1), SIMD3(x0, 0, z0)
                )

                // Skirt walls: only where this cell actually borders empty space
                // or the perimeter. A filled neighbour needs no wall at all — the
                // smoothed corners it shares with this cell already meet there.
                if !occupied(i + 1, j) {
                    quad(SIMD3(x1, h10, z0), SIMD3(x1, h11, z1), SIMD3(x1, 0, z1), SIMD3(x1, 0, z0))
                }
                if !occupied(i - 1, j) {
                    quad(SIMD3(x0, h01, z1), SIMD3(x0, h00, z0), SIMD3(x0, 0, z0), SIMD3(x0, 0, z1))
                }
                if !occupied(i, j + 1) {
                    quad(SIMD3(x1, h11, z1), SIMD3(x0, h01, z1), SIMD3(x0, 0, z1), SIMD3(x1, 0, z1))
                }
                if !occupied(i, j - 1) {
                    quad(SIMD3(x0, h00, z0), SIMD3(x1, h10, z0), SIMD3(x1, 0, z0), SIMD3(x0, 0, z0))
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
