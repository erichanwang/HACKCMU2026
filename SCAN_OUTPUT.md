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
| `suitcaseId` | Id of the suitcase this item was scanned into. Required by `POST /items` (the server rejects an item with none); the suitcase must already exist and belong to the requesting user. `null` afterwards means the item is in the inventory but in no bag (its suitcase was deleted, or it was taken out); `PATCH /items/{id}` with `suitcaseId` moves it. |
| `dimensions` | `[width, height, depth]` of the minimum-area bounding box. Width and depth are the footprint on the table; height is its surface above the table (a 98th-percentile extent on every axis, so LiDAR jitter and stray mesh spikes do not inflate it). Includes a small padding (default 0.5 cm) because LiDAR reads slightly inside true edges. |
| `cellSize` | Side length of one heightmap cell (default 0.01 m). |
| `footprint` | Optional: up to 16 `[x, z]` vertices in metres of the object's convex footprint outline, in the box's local frame relative to its centre (X along `width`, Z along `depth`). Lets the physics gate use a prism instead of the full box for L-shapes and ovals; absent means the footprint is the whole box. |
| `heights` | 2D grid, `ceil(width / cellSize)` rows × `ceil(depth / cellSize)` columns. `heights[i][j]` is the height of the object's surface above the table at that cell. `0` means nothing is there. |
| `label` | Short name of the object. Guessed from the photo by Grok (`labelSource: "auto"`) or typed by the user (`"user"`). |
| `description` | One sentence from Grok: what the object is, its material, anything that matters for packing. Display only. |
| `mass` | Grok's estimated mass in kg, clamped to `[0, 50]`; `0` = unknown. The solver uses it for centre-of-mass balancing. |
| `keepUpright` | `true` if the object must stay this side up (liquids, open containers). The solver then never lays it on its side. |
| `rigidity` | `rigid`, `soft` (compressible — clothes, bags) or `fragile` (breaks if crushed/dropped; the solver stacks nothing on it). Guessed by Grok or chosen by the user; `rigiditySource` says which. A user choice is never overwritten by detection. |
| `compressibility` | `k` ≥ 1: the item's loose volume divided by its volume when squeezed hard (1 = doesn't compress; a t-shirt ≈ 2, a down jacket ≈ 3). Guessed per item by Grok, clamped to `[1, 10]`, always `1` unless `rigidity` is `soft`; `compressibilitySource` says whether it was `auto` or `user`. The solver packs a soft item at `height / k` (`packer3d.Item.compressed`). |
| `labelStatus` | `"done"` once a model has named the item, `"pending"` while the server is still retrying it in the background (every configured model's call itself failed — down or rate-limited — at upload), `"failed"` after `LABEL_MAX_ATTEMPTS` retries, `"unidentified"` when every configured model actually answered but confidently could not name the object — terminal, like `"failed"`, since retrying the same stored photo cannot change a model's mind; `identifyHint` carries a short reason to show the user ("try rotating it" vs "try rescanning it", depending on whether the scan geometry itself looks degenerate). Poll `GET /items/{id}` while it is `"pending"` and redisplay the item when it turns `"done"`, `"failed"` or `"unidentified"`. |
| `photo` | The uploaded JPEG, kept in the document so the background labeller can retry it. Stored server-side only — no route ever returns it. |
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

1. ARKit reconstructs a live mesh from LiDAR and detects the table as a horizontal plane.
2. The tap raycasts onto the object to get a seed point.
3. Mesh triangles near the seed and above the table are sampled densely into a point cloud.
4. Points are flood-filled from the seed through a 2 cm grid so neighbouring objects are excluded.
5. A minimum-area rectangle is fitted to the footprint → `width`, `depth`; the trimmed (98th-percentile) extents → `width`, `depth`, `height`.
6. Each point is dropped into its cell and the maximum height per cell is kept → `heights`.
7. The camera view is cropped to the object and sent with the JSON to `POST /items`; the server asks Grok for `label`, `description`, `rigidity`, `compressibility`, `mass` and `keepUpright`, stores the document, and returns it. Edits in the app go to `PATCH /items/{id}`.

## Server

`server/main.py` — FastAPI + pymongo; it pings MongoDB at startup and exits immediately if it can't be reached (`SUITCASE_MONGODB_URI`, default `mongodb://localhost:27017` — `docker run --rm -d -p 27017:27017 mongo:7` for local dev). Run with `cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0`; nothing else loads `.env`.

Environment: `SUITCASE_MONGODB_URI`, `MONGO_DB` (default `suitcase`), `XAI_API_KEY`/`ANTHROPIC_API_KEY` (either or both; neither set → label `unknown`/`labelStatus: "unidentified"`, rigidity `rigid`, compressibility `1`, mass `0` — with both set, Claude's answer wins whenever the two name the item differently, and either one identifying it is enough), `GROK_MODEL` (default `grok-4`), `ANTHROPIC_MODEL` (default `claude-sonnet-5`), `LABEL_RETRY_S` (default 10s, how often the background labeller re-sweeps `pending` items), `LABEL_MAX_ATTEMPTS` (default 5, after which a still-failing item's `labelStatus` becomes `"failed"`), `AUTH0_DOMAIN`/`AUTH0_AUDIENCE` (both unset → open mode: every request runs as one shared local user, no bearer token required).

Routes beyond `POST /items` (this document's payload) and `GET /items`: `GET /items/{id}` (poll this while `labelStatus` is `"pending"`), `PATCH /items/{id}`, `DELETE /items/{id}`, `GET /inventory` (the caller's items across suitcases, newest first); `POST /suitcases` (`{name, dimensions}`), `GET /suitcases`, `GET /suitcases/{id}` (includes its items), `DELETE /suitcases/{id}` (drops the stored plan and detaches its items, which stay in the owner's inventory); `POST /suitcases/{id}/plan` (runs the solver and stores the result) and `GET /suitcases/{id}/plan` (the stored result, 404 until one exists).

The phone's server address is typed into the app's settings sheet and kept in `UserDefaults`;
`API.base` (`Spike/API.swift`) reads that first, then a `PACKAR_SERVER` scheme variable, then a
hard-coded fallback.
