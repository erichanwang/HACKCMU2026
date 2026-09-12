# User inventory: every scanned item lands in it

2026-09-12. Eric: "on the app every time a new item is scanned add it to a user inventory."

## What exists

Every `POST /items` already stores the scan in Mongo `items` with `owner_id` (the Auth0 sub,
or `"local"` in open mode) and `suitcaseId`. The app only ever reads items per suitcase
(`GET /items?suitcaseId=`), shows them in the Items sheet, and `DELETE /suitcases/{id}`
(the app's Reset) deletes them. The `Items (N)` count on the main screen is stale until the
sheet is opened. So the per-user store exists; what is missing is a user-scoped read, a
lifetime longer than one suitcase, and an app view that grows as you scan.

## Design

Inventory = the requesting user's items, across all suitcases, newest first. It is a read
model over `items`; nothing is duplicated, so labels that arrive later (background Grok
retries, PATCH overrides) are visible in the inventory for free.

### Server (`server/main.py`)

- `GET /inventory` (auth required): `[public(d) for d in db.items.find({"owner_id": user}).sort("createdAt", -1)]`.
- `DELETE /suitcases/{id}`: still deletes the suitcase and its plan; the items are **detached,
  not deleted**: `db.items.update_many({"suitcaseId": suitcase_id}, {"$set": {"suitcaseId": None}})`.
  So `GET /items?suitcaseId=<deleted>` and `GET /suitcases/<deleted>` keep their meaning (no
  orphan class), the plan can never point at moved items, and the inventory survives Reset.
- `Scan.suitcaseId` stays required at upload. `DELETE /items/{id}` unchanged (removes it from
  the inventory too; that is the user's explicit delete).

### App (`Spike/`)

- `API.inventory() async throws -> [ScannedItem]`: `GET inventory`, decoded like `items(suitcaseId:)`.
- `ScannedItem` gains `Equatable` (all fields already are) so `.onChange(of: item)` compiles.
- `ContentView`:
  - `@State private var inventory: [ScannedItem] = []`, loaded from the server when the view
    appears and again each time the sheet opens (same pattern as `items`/`loadItems`).
  - `.onChange(of: item)`: when the new value has `createdAt != nil` (i.e. it is the server's
    document, not the pre-upload local scan), upsert it by id into `inventory` (replace in
    place, else insert at index 0) and into `items` (replace in place, else append). The
    label polling in ScanView updates `item` again when Grok answers, so the same upsert
    refreshes the label.
  - `Inventory (N)` button next to `Items (N)`, always enabled (no suitcase needed to look at
    your things). Sheet lists `inventory` with the same rows as the Items sheet; swipe to
    delete calls `API.delete(itemId:)`. Factor the row/list into one view used by both sheets
    rather than copying it.
  - `reset()` does not touch `inventory`.
- `ScanView.swift` is not modified.

### Docs

- README.md route table: add `GET /inventory`; reword `DELETE /suitcases/{id}` (drops the
  suitcase and its plan, detaches its items so they stay in the owner's inventory).
- SCAN_OUTPUT.md line 104 route list: same two changes.
- docs/DEMO_SCRIPT.md: unrelated teammate request landed in the same batch: the demo launch
  line becomes `PLAN_ITERATIONS=<N> make server` (see FIXES.md section 3).

### Tests

- `server/check.py`: after the two `POST /items` for u1, `GET /inventory` returns them newest
  first; as u2 it returns `[]`; after `DELETE /suitcases/{id}` the items are still in u1's
  inventory with `suitcaseId == None`, and the existing `GET /items?suitcaseId=` == `[]`
  assertion still holds.
- Swift: `tests/swift/api/run.sh` (unchanged helpers still pass), `tests/swift/typecheck/run.sh`
  (API.swift + Geometry.swift + ScanView.swift typecheck), `swiftc -parse Spike/SpikeApp.swift`.
  The app itself builds only on the teammates' Macs.

## Known corner

An item whose upload fails never gets `createdAt`, so it never enters the inventory; an item
deleted from the Inventory sheet is also gone from the Items sheet on its next open. Both
intended.

## Execution split (5 agents, disjoint files, no agent commits; cf commits)

1. opus, server: `server/main.py`, `server/check.py`. Verify `cd server && uv run python check.py`.
2. sonnet, API: `Spike/API.swift`. Verify `tests/swift/api/run.sh`.
3. opus, app: `Spike/SpikeApp.swift`, `Spike/Geometry.swift`. Verify `swiftc -parse`.
4. sonnet, docs: `README.md`, `SCAN_OUTPUT.md`, `docs/DEMO_SCRIPT.md`.
5. opus, review after 1-4: run every check above plus the typecheck harness, read the whole diff
   against this spec, report findings; fixes go back to the owning agent.
