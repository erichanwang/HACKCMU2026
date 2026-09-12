"""Smoke test against a local Mongo. Run: MONGO_DB=suitcase_test uv run python check.py"""
import os
os.environ.setdefault("MONGO_DB", "suitcase_test")
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
# Rescanning (re-POST with the same id) keeps user-set label/rigidity but refreshes geometry.
r = c.post("/items", data={"item": item.replace('"heights": [[0.1]]', '"heights": [[0.2]]')}, files=img).json()
assert r["label"] == "hair dryer" and r["labelSource"] == "user" and r["rigidity"] == "fragile" and r["heights"] == [[0.2]], r
assert len(c.get("/items").json()) == 1
# /label identifies without storing (no key here → unknown).
g = c.post("/label", files=img).json()
assert g["label"] == "unknown" and g["rigidity"] in main.RIGIDITIES and len(c.get("/items").json()) == 1, g
# Confirmed item: no image, app passes the guess through with its sources.
conf = '{"id": "t3", "suitcaseId": "%s", "dimensions": [0.1, 0.1, 0.1], "cellSize": 0.01, "heights": [[0.1]], "label": "mug", "labelSource": "auto", "rigidity": "fragile", "rigiditySource": "auto", "mass": 0.3, "keepUpright": true}' % sc["id"]
r = c.post("/items", data={"item": conf}).json()
assert r["label"] == "mug" and r["labelSource"] == "auto" and r["rigiditySource"] == "auto" and r["mass"] == 0.3 and r["keepUpright"] is True, r
assert c.delete("/items/t3").status_code == 200
# Typed-in item: no image, label and rigidity from the user are kept as user-sourced.
typed = '{"id": "t2", "suitcaseId": "%s", "dimensions": [0.28, 0.005, 0.22], "cellSize": 0.01, "heights": [[0.005]], "label": "iPad", "rigidity": "fragile"}' % sc["id"]
r = c.post("/items", data={"item": typed}).json()
assert r["label"] == "iPad" and r["labelSource"] == "user" and r["rigidity"] == "fragile" and r["rigiditySource"] == "user", r
assert c.delete("/items/t2").status_code == 200
assert c.delete("/items/nope").status_code == 404
assert c.delete("/items/t1").json() == {"deleted": "t1"} and c.get("/items").json() == []
r = c.post("/items", data={"item": item}, files=img); assert r.status_code == 200
assert c.delete(f"/suitcases/{sc['id']}").status_code == 200
assert c.get("/suitcases").json() == [] and c.get("/items").json() == [], "cascade delete"
assert c.delete(f"/suitcases/{sc['id']}").status_code == 404
main.db.items.drop(); main.db.suitcases.drop()
print("server ok")
