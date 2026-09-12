# Scan Output — how a scanned object is represented

Each tap in the LiDAR spike produces one `ScannedItem` (defined in `Spike/Geometry.swift`). For now it is shown on screen and printed to the Xcode console as JSON, preceded by an ASCII map. Nothing is persisted yet.

## The representation

An item is its **bounding box** plus a **heightmap** of its real shape inside that box. All values are in **centimetres**.

```json
{
  "id": "6F3A…",
  "width":  21.3,
  "depth":  12.1,
  "height": 8.4,
  "cellSize": 1.0,
  "heights": [
    [8.4, 8.4, 8.3, 0.0, 0.0, …],
    [8.4, 8.4, 8.2, 0.0, 0.0, …],
    …
  ]
}
```

| Field | Meaning |
|---|---|
| `id` | UUID, unique per scan |
| `width`, `depth`, `height` | Minimum-area bounding box. `width` and `depth` are the footprint on the table; `height` is the tallest point above the table. Includes a small padding (default 0.5 cm) because LiDAR reads slightly inside true edges. |
| `cellSize` | Side length of one heightmap cell (default 1.0 cm). |
| `heights` | 2D grid, `ceil(width / cellSize)` rows × `ceil(depth / cellSize)` columns. `heights[i][j]` is the height of the object's surface above the table at that cell. `0` means nothing is there. |

### Coordinate convention

- `i` (outer index) runs along **width**, `j` (inner index) along **depth**.
- The grid is axis-aligned to the object's own bounding box, not to the room. Where the object sits or how it was rotated on the table is deliberately discarded — the packer decides orientation.
- The object rests on `y = 0` (the table). Height is measured upward from there.

### From heightmap to solid

The object is the volume under the heightmap: cell `(i, j)` is filled from `0` up to `heights[i][j]`. To get a voxel grid, use `cellSize` as the vertical step too:

```
filled(i, j, k)  ⇔  k * cellSize < heights[i][j]
```

For a plain box, every cell equals `height` and the solid is the full bounding box. For a shoe, cells over the opening are lower than the rim. For an L-shaped object, cells in the missing corner are `0`.

### ASCII map

The console prints one character per cell before the JSON, darker = taller (` .:-=+*#%@`), so the shape can be checked at a glance:

```
@@@@@@@@@@@
@@@@@@@@@@@
@@@@@@
@@@@@@
```

## What it captures and what it doesn't

The scan is a **single top-down view**, so the shape is 2.5D:

- Captured: the footprint outline, height variation across the top, openings and dips visible from above (an open shoe, a bag's slope, a hole through the middle).
- Not captured: undercuts or overhangs hidden from the camera. A mushroom-shaped object is recorded as a cylinder as wide as its cap.
- Resolution is `cellSize`; anything smaller than a cell is smoothed over. LiDAR accuracy is roughly ±1 cm, so thin items (a flat book) register but with noisy height.

Tuning constants live at the top of `Spike/ScanView.swift`: `paddingMeters`, `minHeightMeters`, `searchRadiusMeters`, `clusterCellMeters`, `shapeCellMeters`.

## How it is produced

1. ARKit reconstructs a live mesh from LiDAR and detects the table as a horizontal plane.
2. The tap raycasts onto the object to get a seed point.
3. Mesh triangles near the seed and above the table are sampled densely into a point cloud.
4. Points are flood-filled from the seed through a 2 cm grid so neighbouring objects are excluded.
5. A minimum-area rectangle is fitted to the footprint → `width`, `depth`; the tallest point → `height`.
6. Each point is dropped into its cell and the maximum height per cell is kept → `heights`.
