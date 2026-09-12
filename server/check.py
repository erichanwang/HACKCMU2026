"""Smoke test. Run: MONGO_DB=suitcase_test uv run python check.py

This DROPS the items and suitcases collections, so it refuses to run against a
database whose name does not end in `_test`. That guard matters because
.env.local points SUITCASE_MONGODB_URI at the shared Atlas cluster: without it,
running this file with the default MONGO_DB would wipe the team's real scans.
Point it at a local mongod to keep the cloud out of it entirely:

    SUITCASE_MONGODB_URI=mongodb://localhost:27017 MONGO_DB=suitcase_test python check.py
"""
import json
import os
import sys
os.environ.setdefault("MONGO_DB", "suitcase_test")
if not os.environ["MONGO_DB"].endswith("_test"):
    sys.exit(f"refusing to run: MONGO_DB={os.environ['MONGO_DB']!r} is not a *_test database, "
             "and this smoke test drops collections")
from fastapi.testclient import TestClient
import main

main.db.items.drop(); main.db.suitcases.drop()
c = TestClient(main.app)

assert c.post("/suitcases", json={"name": "x", "dimensions": [0.5, 0]}).status_code == 422
sc = c.post("/suitcases", json={"name": "carry-on", "dimensions": [0.55, 0.22, 0.35]}).json()
assert sc["id"] and sc["dimensions"] == [0.55, 0.22, 0.35], sc
assert c.get("/suitcases").json()[0]["id"] == sc["id"]

item = '{"id": "t1", "suitcaseId": "%s", "dimensions": [0.2, 0.1, 0.1], "cellSize": 0.01, "heights": [[0.1]]}' % sc["id"]
img = {"image": ("o.jpg", b"\xff\xd8fake", "image/jpeg")}
assert c.post("/items", data={"item": item.replace(sc["id"], "ghost")}, files=img).status_code == 404
r = c.post("/items", data={"item": item}, files=img)
assert r.status_code == 200, r.text
assert r.json()["labelSource"] == "auto" and r.json()["rigidity"] in main.RIGIDITIES, r.json()

r = c.patch("/items/t1", json={"label": "hair dryer", "rigidity": "fragile"})
assert r.json()["label"] == "hair dryer" and r.json()["rigiditySource"] == "user", r.json()
assert c.patch("/items/t1", json={"rigidity": "wet"}).status_code == 422
assert c.patch("/items/nope", json={"label": "x"}).status_code == 404
assert c.post("/items", data={"item": "not json"}, files={"image": ("o.jpg", b"", "image/jpeg")}).status_code == 400

items = c.get("/items").json()
assert len(items) == 1 and items[0]["heights"] == [[0.1]] and "_id" not in items[0], items
assert c.get("/items", params={"suitcaseId": "ghost"}).json() == []
full = c.get(f"/suitcases/{sc['id']}").json()
assert full["name"] == "carry-on" and [i["id"] for i in full["items"]] == ["t1"], full
assert c.get("/suitcases/ghost").status_code == 404

# --- POST /pack ---------------------------------------------------------------
CASE = [0.55, 0.22, 0.35]
for iid, dims, rigidity in [
    ("laptop", [0.36, 0.03, 0.25], "fragile"), ("jeans", [0.35, 0.06, 0.28], "soft"),
    ("shoes", [0.32, 0.12, 0.20], "rigid"), ("camera", [0.13, 0.09, 0.10], "fragile"),
]:
    body = '{"id": "%s", "suitcaseId": "%s", "dimensions": %s, "cellSize": 0.01, "heights": [[%s]]}' % (
        iid, sc["id"], dims, dims[1])
    assert c.post("/items", data={"item": body}, files=img).status_code == 200
    c.patch(f"/items/{iid}", json={"label": iid, "rigidity": rigidity})

r = c.post("/pack", json={"dimensions": CASE, "maxMassKg": 23, "suitcaseId": sc["id"], "timeBudgetS": 2})
assert r.status_code == 200, r.text
pack = r.json()
assert "naive" not in pack and "optimized" not in pack, "must return the bare optimised result"
assert pack["verifyErrors"] == [], pack["verifyErrors"]
assert {p["id"] for p in pack["placements"]} | {u["id"] for u in pack["unpacked"]} >= {"laptop", "jeans", "shoes", "camera"}
assert all(u.get("reason") for u in pack["unpacked"]), "unpacked entries must carry a reason"

# Rigidity survives the round trip, and mass is flagged as estimated (no scan gives mass).
by_id = {p["id"]: p for p in pack["placements"]}
assert by_id["jeans"]["rigidity"] == "soft" and by_id["laptop"]["rigidity"] == "fragile", by_id
assert all(p["massEstimated"] and p["massKg"] > 0 for p in pack["placements"]), pack["placements"]

# Every placement sits inside the container in the convention the response declares:
# x in [0, w], y in [0, h] (floor at 0), z in [-d, 0].
W, H, D = CASE
for p in pack["placements"]:
    (cx, cy, cz), (dx, dy, dz) = p["position"], p["dimensions"]
    assert -1e-6 <= cx - dx / 2 and cx + dx / 2 <= W + 1e-6, p
    assert -1e-6 <= cy - dy / 2 and cy + dy / 2 <= H + 1e-6, p
    assert -D - 1e-6 <= cz - dz / 2 and cz + dz / 2 <= 1e-6, p

assert c.post("/pack", json={"dimensions": [0.5, 0]}).status_code == 422
assert c.post("/pack", json={"dimensions": CASE, "suitcaseId": "ghost"}).status_code == 404
assert c.post("/pack", json={"dimensions": CASE, "itemIds": ["nope"]}).status_code == 404
subset = c.post("/pack", json={"dimensions": CASE, "itemIds": ["laptop", "camera"], "timeBudgetS": 1}).json()
assert len(subset["placements"]) == 2, subset


# --- voxels go in their own JSON body, not the multipart form -------------------
vox = {"voxelSize": 0.005, "origin": [0.0, 0.0, 0.0],
       "indices": [0, 0, 0, 1, 0, 0], "colours": [255, 0, 0, 0, 0, 255],
       "observations": [3, 4], "colourCoverage": 1.0}
r = c.put("/items/t1/voxels", json={"voxels": vox, "viewCoverage": 0.75})
assert r.status_code == 200, r.text
assert r.json()["voxels"] == vox and r.json()["voxelCount"] == 2, r.json()
assert r.json()["viewCoverage"] == 0.75, r.json()
assert next(i for i in c.get("/items").json() if i["id"] == "t1")["voxels"] == vox

assert c.put("/items/nope/voxels", json={"voxels": vox}).status_code == 404
assert c.put("/items/t1/voxels", json={"voxels": dict(vox, indices=[0, 0])}).status_code == 422
assert c.put("/items/t1/voxels", json={"voxels": dict(vox, observations=[1])}).status_code == 422
assert c.put("/items/t1/voxels", json={"voxels": dict(vox, colours=[1, 2, 3])}).status_code == 422
assert c.put("/items/t1/voxels", json={"voxels": dict(vox, voxelSize=0)}).status_code == 422

# A payload far bigger than the 1 MB multipart cap must still get through, since that is
# the whole reason this endpoint exists; only a genuinely runaway scan is refused.
big = {"voxelSize": 0.005, "origin": [0.0, 0.0, 0.0],
       "indices": [0, 1, 2] * 60_000, "colours": [], "observations": [2] * 60_000,
       "colourCoverage": 0.0}
r = c.put("/items/t1/voxels", json={"voxels": big})
assert r.status_code == 200, r.text[:200]
assert r.json()["voxelCount"] == 60_000, r.json()["voxelCount"]

runaway = dict(big, indices=[0, 1, 2] * 400_001, observations=[2] * 400_001)
assert c.put("/items/t1/voxels", json={"voxels": runaway}).status_code == 413

main.db.items.drop(); main.db.suitcases.drop()
print("server ok")
