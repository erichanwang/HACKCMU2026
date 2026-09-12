# Demo script — 3 minutes, HackCMU judges

Written from the code as it stands. If a step here doesn't match what's on screen, the code
moved — fix this file, don't fake the demo.

## 1. Setup, 15 minutes before

On the Mac:

```sh
docker run --rm -d --name suitcase-mongo -p 27017:27017 mongo:7
cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0 --workers 2
```

(`make mongo` / `make server` do the same two steps if `.env` is at the repo root.)

Get the Mac's LAN IP and sanity-check the server before touching the phone:

```sh
ipconfig getifaddr en0
curl http://<that-ip>:8000/suitcases   # should return [] or existing suitcases, not a connection error
```

On the phone: open the app, tap the URL button at the bottom of the screen (shows the current
`serverURL`), type `http://<that-ip>:8000` into the Settings sheet. No rebuild needed.

Checklist:
- Phone and Mac on the **same Wi-Fi**, not phone hotspot / Mac Ethernet.
- Phone is a **LiDAR** iPhone or iPad Pro (`ARWorldTrackingConfiguration.sceneReconstruction =
  .mesh` throws without it).
- A real suitcase, **open**, on a table (not the floor — easier to scan, easier for judges to
  see).
- 4–6 items laid out next to it: mix rigid (book, camera), soft (t-shirt), fragile (something
  Grok will call fragile) — verified today, Grok answers in ~6s per item and labels this mix
  reliably.
- `XAI_API_KEY` is in `.env` — without it every item comes back "unknown" and nothing above
  matters.

## 2. The script

**Beat 1 — problem (15s).** "Packing a suitcase well is bin-packing most people do badly by
eye. PackAR scans your actual bag and your actual stuff and tells you exactly where each thing
goes — then shows you in AR."

**Beat 2 — scan suitcase (20s).** Suitcase mode is selected by default. Tap the open, empty
suitcase. Status goes "Creating suitcase…" → "Suitcase captured — switch to Item and tap what
goes in." Say: "that's the bounding box of the bag — the app doesn't know about wheel wells or
lid pockets yet, so it's a few centimetres optimistic."

**Beat 3 — scan items (~30-45s for 4-6 items).** Switch to Item. Tap each object in turn.
Status shows point/cell counts, then **"labelling…"** — that's the server calling Grok
synchronously inside the upload, ~6s per item. Say while it's running: "server's asking an LLM
what this is and how it should be packed — rigid, soft, or fragile." When it resolves: "labelled
<X>" and the label/rigidity picker appears under the item so you can correct Grok live if it
guessed wrong.

**Beat 4 — Pack (5-10s).** Tap **Pack**. Button is disabled while a plan is in flight. Status:
"Packing…" then "Packed N items" (or "…didn't fit: <label>" if something didn't fit — say what
that means, don't hide it). This is the solver running four candidate packings plus a physics
validation pass, picking the best.

**Beat 5 — 2D/3D plan (15s).** The plan diagram sheet opens automatically. Walk through it
layer by layer: "each item's position and rotation, checked for collisions, containment, and
support before you ever see it."

**Beat 6 — AR overlay (20s).** Close the sheet, back to the AR view. Coloured boxes should
appear inside the scanned bag showing where each item goes. Say what you're pointing at: "this
green box is where the book goes." (This step has never run on a real device before today — see
fallback below if it doesn't render.)

## 3. Fallbacks

- **Grok down / times out.** Item shows label `"unknown"`; type the real label into the field
  under the item (`ItemEditor`) — it PATCHes the override immediately, no need to wait or
  retry. Keep going, don't stop the demo for it.
- **Wi-Fi dies mid-demo.** Switch to the pre-recorded fallback video. Don't debug Wi-Fi live in
  front of judges.
- **AR overlay off by a few cm.** Say why up front, don't get caught flat-footed: "the suitcase
  scan captures the outer shell, walls and lid included — `suitcaseWallMeters` in
  `Spike/ScanView.swift` is the calibration knob for that, currently 1cm." That's an honest,
  specific answer, not an apology.
- **An item doesn't fit.** The status line already names it: "Packed N, didn't fit: <label>."
  Point at the line and say the solver reports failures instead of silently dropping items.
- **Server crashes.** Restart with the same two commands from Setup:
  `docker start suitcase-mongo` (if the container stopped) then
  `cd server && uv run --env-file ../.env uvicorn main:app --host 0.0.0.0 --workers 2`. Re-point the app at
  the URL only if the IP changed — it didn't need re-typing otherwise.

## 4. Judge questions — honest answers

- **"Is that PAN world model actually running?"** Two layers exist: a visual mock
  (`pan/world_model.py`, holds the scene still with synthetic noise — not a real render) and a
  text layer that really calls IFM's hosted `K2-Horizon-375B-A23B` model (`physics/pan.py`) for
  a risk read on the plan. Neither is wired into the iOS app today — say "physics-checked plan
  with an LLM risk read," not "we simulate the pack visually before you do it."
- **"What checks the plan is actually physically valid?"** `server/planner.py` runs the packer3d
  solver four ways (one naive, three optimised with different seeds), validates each candidate
  against collisions/containment/support/fragility, and keeps the best-ranked one. Runner-up
  candidates are scored but their placements aren't stored, so there's no "here's plan B" in the
  UI yet.
- **"How much noise does LiDAR add?"** Untested on a real device as of this morning — the scan
  geometry math (`Spike/Geometry.swift`) is checked against clean synthetic point clouds on
  Linux (`tests/swift/scan/`), not real ARKit mesh anchors. Say that plainly if asked; don't
  claim device-verified noise handling.
- **"What's Python vs. Swift?"** Server, solver, and physics validation are Python
  (`server/`, `packer3d/`, `physics/`) reachable over HTTP. The iOS app (`Spike/`) is Swift/
  SwiftUI/ARKit/RealityKit and only talks to the server over that HTTP API — no packing math
  runs on the phone. `swift/PackPhysics` is a from-scratch Swift port of the physics validator
  for a future on-device path, not currently called by the app.
