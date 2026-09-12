# Suitcase — AR Packing Assistant

Scan objects with iPhone LiDAR, store them in MongoDB, pack them optimally, guide the pack in AR. See `MVP.md` for the demo plan and team split, `SCAN_OUTPUT.md` for the scanned-object format.

```
Spike/     iOS app — LiDAR scan → ScannedItem → POST to server
server/    FastAPI + MongoDB — stores items, labels them with Grok
Tests/     self-check for the geometry code
```

## Server

Needs [uv](https://docs.astral.sh/uv/) (`brew install uv`). Everything else installs itself.

```bash
cd server
export SUITCASE_MONGODB_URI='mongodb+srv://…'   # Atlas connection string — ask Helen
export XAI_API_KEY='…'                           # Grok key — ask Helen; omit and items are labelled "unknown"
uv run uvicorn main:app --host 0.0.0.0
```

Runs on port 8000. Endpoints:

| | |
|---|---|
| `POST /suitcases` | JSON `{"name": …, "dimensions": [w, h, d]}` in metres. Returns the suitcase with its `id`. |
| `GET /suitcases` | All suitcases, newest first. |
| `GET /suitcases/{id}` | One suitcase **with its `items`** — the solver's input. |
| `POST /items` | multipart: `item` (ScannedItem JSON) + `image` (JPEG). Labels via Grok, stores, returns the item. |
| `PATCH /items/{id}` | JSON `{"label": …, "rigidity": "rigid"\|"soft"\|"fragile"}` — user override. |
| `GET /items?suitcaseId=…` | Scanned items, optionally filtered by suitcase. |

Optional env: `MONGO_DB` (default `suitcase`), `GROK_MODEL` (default `grok-4`). Without `SUITCASE_MONGODB_URI` it uses a local `mongodb://localhost:27017`.

Smoke test (needs a reachable Mongo): `uv run python check.py` → prints `server ok`.

## iOS app

Needs Xcode 16+, an iPhone with LiDAR (any Pro model), and `xcodegen` (`brew install xcodegen`). The `.xcodeproj` is not committed — generate it:

```bash
xcodegen generate
open Spike.xcodeproj
```

Then in Xcode: select the **Spike** target → **Signing & Capabilities** → choose your Team. Plug in the phone, pick it as the destination, ⌘R.

Before running, point the app at the machine running the server: edit `API.base` in `Spike/API.swift` to that Mac's LAN IP (`ipconfig getifaddr en0`). Phone and Mac must be on the same Wi-Fi.

Gotchas:
- If Xcode says the bundle identifier is not available, change `bundleIdPrefix` in `project.yml` to something unique to you and re-run `xcodegen generate`.
- First run on a phone: enable **Settings → Privacy & Security → Developer Mode**, then trust your certificate under **Settings → General → VPN & Device Management**.

Using it: create or pick a suitcase (interior dimensions in cm), then point at an object on a table, pan for a couple of seconds until the mesh overlay covers it, tap the object. The Xcode console prints an ASCII heightmap and the JSON; the screen shows dimensions and the label/rigidity guess, both editable.

## Geometry self-check

```bash
swiftc -O Spike/Geometry.swift Tests/main.swift -o /tmp/geocheck && /tmp/geocheck
```

## Conventions

- Metres everywhere. `dimensions = [width, height, depth]`, X right, Y up, Z forward.
- Work on a branch, open a PR to `main`. No co-author trailers on commits.
- Never commit credentials. Env vars only.
