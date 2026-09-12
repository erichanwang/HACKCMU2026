"""Smoke test against a local Mongo. Run: MONGO_DB=suitcase_test uv run python check.py"""
import json
import math
import os
os.environ.setdefault("MONGO_DB", "suitcase_test")
from fastapi.testclient import TestClient
import main

ROTATIONS = {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}  # AxisRotation.swift

main.db.items.drop(); main.db.suitcases.drop(); main.db.plans.drop()
c = TestClient(main.app)


def scan(item_id: str, suitcase_id: str, w: float, h: float, d: float) -> str:
    """A full-box scan document: heights is ceil(w/cell) x ceil(d/cell) cells of h (SCAN_OUTPUT.md)."""
    cell = 0.01
    rows, cols = math.ceil(w / cell), math.ceil(d / cell)
    return json.dumps({"id": item_id, "suitcaseId": suitcase_id, "dimensions": [w, h, d],
                       "cellSize": cell, "heights": [[h] * cols for _ in range(rows)]})

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
assert r.json()["compressibility"] >= 1 and r.json()["compressibilitySource"] == "auto", r.json()
assert r.json()["mass"] >= 0 and isinstance(r.json()["keepUpright"], bool) and "description" in r.json(), r.json()

r = c.patch("/items/t1", json={"label": "hair dryer", "rigidity": "fragile", "compressibility": 2.5})
assert r.json()["label"] == "hair dryer" and r.json()["rigiditySource"] == "user", r.json()
assert r.json()["compressibility"] == 2.5 and r.json()["compressibilitySource"] == "user", r.json()
assert c.patch("/items/t1", json={"rigidity": "wet"}).status_code == 422
assert c.patch("/items/t1", json={"compressibility": 0.5}).status_code == 422
assert main.compressibility("3", "soft") == 3.0 and main.compressibility(3, "rigid") == 1.0
assert main.compressibility("lots", "soft") == 1.0 and main.compressibility(99, "soft") == 10.0
assert c.patch("/items/nope", json={"label": "x"}).status_code == 404
assert c.post("/items", data={"item": "not json"}, files={"image": ("o.jpg", b"", "image/jpeg")}).status_code == 400

items = c.get("/items").json()
assert len(items) == 1 and items[0]["heights"] == [[0.1]] and "_id" not in items[0], items
assert c.get("/items", params={"suitcaseId": "ghost"}).json() == []
full = c.get(f"/suitcases/{sc['id']}").json()
assert full["name"] == "carry-on" and [i["id"] for i in full["items"]] == ["t1"], full
assert c.get("/suitcases/ghost").status_code == 404

# --- packing plan ---------------------------------------------------------------
assert c.get(f"/suitcases/{sc['id']}/plan").status_code == 404  # nothing solved yet
assert c.get("/suitcases/ghost/plan").status_code == 404
empty = c.post("/suitcases", json={"name": "empty", "dimensions": [0.55, 0.22, 0.35]}).json()
assert c.post(f"/suitcases/{empty['id']}/plan").status_code == 409, "a suitcase with no items cannot be planned"
assert c.post("/suitcases/ghost/plan").status_code == 404

for item_id, (w, h, d) in {"t2": (0.3, 0.1, 0.2), "t3": (0.25, 0.12, 0.18), "t4": (0.2, 0.08, 0.15)}.items():
    r = c.post("/items", data={"item": scan(item_id, sc["id"], w, h, d)}, files=img)
    assert r.status_code == 200, r.text
assert c.patch("/items/t3", json={"rigidity": "soft", "compressibility": 2}).json()["compressibility"] == 2
r = c.patch("/items/t4", json={"mass": 1.5, "keepUpright": True}).json()
assert r["mass"] == 1.5 and r["keepUpright"] is True, r
assert c.patch("/items/t4", json={"mass": -1}).status_code == 422

r = c.post(f"/suitcases/{sc['id']}/plan")
assert r.status_code == 200, r.text
plan = r.json()
assert plan["suitcaseId"] == sc["id"] and "_id" not in plan and plan["createdAt"], plan
assert plan["solver"]["metrics"]["items_packed"] >= 1, plan["solver"]["metrics"]
assert "valid" in plan["validation"] and "adapter" in plan["validation"], plan["validation"]
assert plan["chosen"]["strategy"] == plan["solver"]["strategy"] == plan["alternatives"][0]["strategy"], plan["chosen"]
assert len(plan["alternatives"]) >= 2 and all("physics_valid" in a for a in plan["alternatives"]), plan["alternatives"]
p = plan["plan"]
assert p["units"] == "meters" and p["container"]["dimensions"] == {"x": 0.55, "y": 0.22, "z": 0.35}, p["container"]
assert p["placements"], p
assert [q["step"] for q in p["placements"]] == list(range(1, len(p["placements"]) + 1)), p["placements"]
zones = {z["id"] for z in p["container"]["zones"]}
by_id = {i["id"]: i for i in c.get("/items").json()}
for q in p["placements"]:
    assert q["zone"] in zones and q["rotation"] in ROTATIONS and q["itemId"] in by_id and q["label"], q
    for axis in "xyz":
        assert q["position"][axis] >= -1e-9, q
        assert q["position"][axis] + q["size"][axis] <= p["container"]["dimensions"][axis] + 1e-9, q
    # size must be the item's own [w, h, d] permuted by `rotation` (AxisRotation.bagExtent)
    it = by_id[q["itemId"]]
    w, h, d = it["dimensions"]
    local = {"X": w, "Y": h / it["compressibility"] if it["rigidity"] == "soft" else h, "Z": d}
    assert all(math.isclose(q["size"][a], local[k], abs_tol=1e-9)
               for a, k in zip("xyz", q["rotation"])), (q, local)
    if it["keepUpright"]:
        assert q["rotation"][1] == "Y", ("keepUpright item was tipped over", q)
assert c.get(f"/suitcases/{sc['id']}/plan").json() == plan, "GET must return the stored plan"

main.db.items.drop(); main.db.suitcases.drop(); main.db.plans.drop()
print("server ok")
