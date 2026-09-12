"""Smoke test against a local Mongo. Run: MONGO_DB=suitcase_test uv run python check.py"""
import os
os.environ.setdefault("MONGO_DB", "suitcase_test")
from fastapi.testclient import TestClient
import main

main.db.items.drop()
c = TestClient(main.app)
item = '{"id": "t1", "dimensions": [0.2, 0.1, 0.1], "cellSize": 0.01, "heights": [[0.1]]}'
r = c.post("/items", data={"item": item}, files={"image": ("o.jpg", b"\xff\xd8fake", "image/jpeg")})
assert r.status_code == 200, r.text
assert r.json()["labelSource"] == "auto" and r.json()["rigidity"] in main.RIGIDITIES, r.json()

r = c.patch("/items/t1", json={"label": "hair dryer", "rigidity": "fragile"})
assert r.json()["label"] == "hair dryer" and r.json()["rigiditySource"] == "user", r.json()
assert c.patch("/items/t1", json={"rigidity": "wet"}).status_code == 422
assert c.patch("/items/nope", json={"label": "x"}).status_code == 404
assert c.post("/items", data={"item": "not json"}, files={"image": ("o.jpg", b"", "image/jpeg")}).status_code == 400

items = c.get("/items").json()
assert len(items) == 1 and items[0]["heights"] == [[0.1]] and "_id" not in items[0], items
main.db.items.drop()
print("server ok")
