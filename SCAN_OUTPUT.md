# Scan Output — how a scanned object is represented

Each tap in the LiDAR spike produces one `ScannedItem` (defined in `Spike/Geometry.swift`). The phone prints it to the Xcode console (ASCII map + JSON), then POSTs it with a photo crop to the server in `server/`, which labels it and stores it in MongoDB (collection `items`, `_id` = `id`). The server's response — the same object plus `label` and `rigidity` — is what the app displays and what the packer reads via `GET /items`.

## The representation

An item is its **bounding box** plus a **heightmap** of its real shape inside that box, plus a **label** and **rigidity**. All lengths are in **metres** (team contract: X right, Y up, Z forward).

```json
{
  "id": "6F3A…",
  "suitcaseId": "9B2C…",
  "dimensions": [0.213, 0.084, 0.121],
  "cellSize": 0.01,
  "heights": [
    [0.084, 0.084, 0.083, 0.0, 0.0, …],
    [0.084, 0.084, 0.082, 0.0, 0.0, …],
    …
  ],
  "label": "running shoe",
  "labelSource": "auto",
  "description": "Mesh running shoe with a rubber sole; the opening can hold socks.",
  "mass": 0.3,
  "keepUpright": false,
  "rigidity": "soft",
  "rigiditySource": "user",
  "compressibility": 2.0,
  "compressibilitySource": "auto",
  "createdAt": "2026-09-12T03:14:15+00:00"
}
```

| Field | Meaning |
|---|---|
| `id` | UUID string, unique per scan; also the Mongo `_id`. |
| `suitcaseId` | The suitcase this item belongs to (`suitcases` collection, below). |
| `dimensions` | `[width, height, depth]` of the minimum-area bounding box. Width and depth are the footprint on the table; height is the tallest point above it. Includes a small padding (default 0.5 cm) because LiDAR reads slightly inside true edges. |
| `cellSize` | Side length of one heightmap cell (default 0.01 m). |
| `heights` | 2D grid, `ceil(width / cellSize)` rows × `ceil(depth / cellSize)` columns. `heights[i][j]` is the height of the object's surface above the table at that cell. `0` means nothing is there. |
| `label` | Short name of the object. Guessed from the photo by Claude (`labelSource: "auto"`) or typed by the user (`"user"`). |
| `description` | One sentence from Claude: what the object is, its material, anything that matters for packing. Display only. |
| `mass` | Claude's estimated mass in kg, clamped to `[0, 50]`; `0` = unknown. The solver uses it for centre-of-mass balancing. |
| `keepUpright` | `true` if the object must stay this side up (liquids, open containers). The solver then never lays it on its side. |
| `rigidity` | `rigid`, `soft` (compressible — clothes, bags) or `fragile` (breaks if crushed/dropped; the solver stacks nothing on it). Guessed by Claude or chosen by the user; `rigiditySource` says which. A user choice is never overwritten by detection. |
| `compressibility` | `k` ≥ 1: the item's loose volume divided by its volume when squeezed hard (1 = doesn't compress; a t-shirt ≈ 2, a down jacket ≈ 3). Guessed per item by Claude, clamped to `[1, 10]`, always `1` unless `rigidity` is `soft`; `compressibilitySource` says whether it was `auto` or `user`. The solver packs a soft item at `height / k` (`packer3d.Item.compressed`). |
| `createdAt` | ISO-8601 UTC timestamp, set by the server. |

### Coordinate convention

- `i` (outer index) runs along **width** (X), `j` (inner index) along **depth** (Z).
- The grid is axis-aligned to the object's own bounding box, not to the room. Where the object sits or how it was rotated on the table is deliberately discarded — the packer decides orientation.
- The object rests on `y = 0` (the table). Height is measured upward from there.

### From heightmap to solid

The object is the volume under the heightmap: cell `(i, j)` is filled from `0` up to `heights[i][j]`. To get a voxel grid, use `cellSize` as the vertical step too:

```
filled(i, j, k)  ⇔  k * cellSize < heights[i][j]
```

For a plain box, every cell equals `dimensions[1]` and the solid is the full bounding box. For a shoe, cells over the opening are lower than the rim. For an L-shaped object, cells in the missing corner are `0`.

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

1. The tap raycasts onto the object to get a seed point. (ARKit's mesh is shown on screen as a coverage cue only.)
2. The frame's LiDAR depth map (256×192, with per-pixel confidence) is back-projected to world points near the seed; low-confidence pixels are dropped.
3. The support surface is the highest 1 cm band of heights holding a large share of those points and clearly below the seed (a lid or box top is often an ARKit plane, so plane anchors are only a fallback). Points above that surface are the object.
4. Points are flood-filled from the seed through a 2 cm grid so neighbouring objects are excluded.
5. A minimum-area rectangle is fitted to the footprint → `width`, `depth`; height from the top of the points. The outermost 1% of points on each side are ignored.
6. Each point is dropped into its cell and the maximum height per cell is kept → `heights`.
7. The camera view is cropped to the object and sent with the JSON to `POST /items`; the server asks Claude for `label`, `description`, `rigidity`, `compressibility`, `mass` and `keepUpright`, stores the document, and returns it. Edits in the app go to `PATCH /items/{id}`.

## Suitcases

Items belong to a suitcase. A suitcase is typed in by the user (interior dimensions) and stored in the `suitcases` collection:

```json
{ "id": "9B2C…", "name": "Carry-on", "dimensions": [0.55, 0.22, 0.35], "createdAt": "…" }
```

`GET /suitcases/{id}` returns the suitcase with an `items` array — the complete input for the packing solver.

## Server

`server/main.py` — FastAPI + pymongo. Run with `cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0 --reload`. Environment: `SUITCASE_MONGODB_URI` (default `mongodb://localhost:27017`), `MONGO_DB` (default `suitcase`), `ANTHROPIC_API_KEY` (no key → label `unknown`, rigidity `rigid`, compressibility `1`, mass `0`), `ANTHROPIC_MODEL` (default `claude-opus-5`). The phone's server address is `API.base` in `Spike/API.swift`.
