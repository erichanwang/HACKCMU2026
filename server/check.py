"""End-to-end check against an in-memory Mongo (mongomock). Run: cd server && uv run python check.py"""
import json
import math
import os
from unittest.mock import patch

import httpx
import mongomock
import pymongo

os.environ.setdefault("MONGO_DB", "suitcase_test")
from fastapi.testclient import TestClient
with patch.object(pymongo, "MongoClient", mongomock.MongoClient):
    import main

import auth

ROTATIONS = {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}  # AxisRotation.swift

main.db.items.drop(); main.db.suitcases.drop(); main.db.plans.drop(); main.db.users.drop()
c = TestClient(main.app)

# Every route below runs "as" u1 unless a block overrides it, since real Auth0 tokens
# aren't available in this environment (see test_auth below for JWT verification itself).
main.app.dependency_overrides[auth.require_auth] = lambda: {"sub": "u1", "email": "u1@example.com"}


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
assert r.json()["labelStatus"] == "done", "no XAI_API_KEY means there is nothing to retry"

r = c.patch("/items/t1", json={"label": "hair dryer", "rigidity": "fragile", "compressibility": 2.5})
assert r.json()["label"] == "hair dryer" and r.json()["rigiditySource"] == "user", r.json()
assert r.json()["compressibility"] == 2.5 and r.json()["compressibilitySource"] == "user", r.json()
assert c.patch("/items/t1", json={"rigidity": "wet"}).status_code == 422
assert c.patch("/items/t1", json={"compressibility": 0.5}).status_code == 422
assert main.compressibility("3", "soft") == 3.0 and main.compressibility(3, "rigid") == 1.0
assert main.compressibility("lots", "soft") == 1.0 and main.compressibility(99, "soft") == 10.0
assert c.patch("/items/nope", json={"label": "x"}).status_code == 404
assert c.post("/items", data={"item": "not json"}, files={"image": ("o.jpg", b"", "image/jpeg")}).status_code == 422
bad = {"id": "bad", "suitcaseId": sc["id"], "dimensions": [0.2, 0.1, 0.1], "cellSize": 0.01, "heights": [[0.1]]}
for broken in ({"dimensions": [0.2, 0.1]}, {"dimensions": [0.2, 0.1, -0.1]}, {"cellSize": 0},
               {"heights": []}, {"heights": [[]]}, {"heights": [[0.1], [0.1, 0.1]]}, {"heights": [[-1]]}, {"id": ""}):
    r = c.post("/items", data={"item": json.dumps(bad | broken)}, files=img)
    assert r.status_code == 422, (broken, r.status_code, r.text)
assert c.get("/items", params={"suitcaseId": sc["id"]}).json()[0]["id"] == "t1", "a refused scan must not be stored"

# Grok down must not lose the scan: the item is saved as "unknown" for the app's editor to fix,
# and marked pending so the background sweep relabels it from the stored photo.
with patch.dict(os.environ, {"XAI_API_KEY": "k"}), patch.object(main.httpx, "post", side_effect=httpx.ConnectError("down")):
    r = c.post("/items", data={"item": json.dumps(bad | {"id": "t1b"})}, files=img)
assert r.status_code == 200 and r.json()["label"] == "unknown" and r.json()["rigidity"] == "rigid", r.text
assert r.json()["labelStatus"] == "pending" and "photo" not in r.json(), r.json()
assert main.db.items.find_one({"_id": "t1b"})["photo"] == b"\xff\xd8fake", "the photo must be kept for the retry"
assert c.get("/items/t1b").json()["labelStatus"] == "pending", "the app polls this while labelling is pending"
assert c.get("/items/ghost").status_code == 404

# --- background labelling sweep (the lifespan task itself only runs under `with TestClient`) ---
def grok(*_a, **_k):
    """One good Grok answer, whatever it is asked."""
    return httpx.Response(200, request=httpx.Request("POST", "https://api.x.ai/v1/chat/completions"),
                          json={"choices": [{"message": {"content": json.dumps(
                              {"label": "wool scarf", "description": "a knitted scarf", "rigidity": "soft",
                               "compressibility": 2, "mass": 0.2, "keepUpright": False})}}]})

assert c.patch("/items/t1b", json={"rigidity": "fragile"}).json()["rigiditySource"] == "user"
with patch.dict(os.environ, {"XAI_API_KEY": "k"}), patch.object(main.httpx, "post", grok):
    assert main.relabel_pending() == 1
    assert main.relabel_pending() == 0, "a labelled item is no longer pending"
d = c.get("/items/t1b").json()
assert d["labelStatus"] == "done" and d["label"] == "wool scarf" and d["mass"] == 0.2, d
assert d["rigidity"] == "fragile", "the sweep must not overwrite a rigidity the user set"
assert "photo" not in d, d

# a photo Grok never manages to read is retried LABEL_MAX_ATTEMPTS times, then given up on
with patch.dict(os.environ, {"XAI_API_KEY": "k"}), patch.object(main.httpx, "post", side_effect=httpx.ConnectError("down")):
    assert c.post("/items", data={"item": json.dumps(bad | {"id": "t1c"})}, files=img).json()["labelStatus"] == "pending"
    for _ in range(main.LABEL_MAX_ATTEMPTS):
        assert main.relabel_pending() == 0
assert c.get("/items/t1c").json()["labelStatus"] == "failed", c.get("/items/t1c").json()
assert main.db.items.find_one({"_id": "t1c"})["labelAttempts"] == main.LABEL_MAX_ATTEMPTS
main.db.items.update_one({"_id": "t1c"}, {"$set": {"labelStatus": "pending"}, "$unset": {"photo": ""}})
assert main.relabel_pending() == 0 and c.get("/items/t1c").json()["labelStatus"] == "failed", "no photo, nothing to retry"

assert c.delete("/items/t1b").json() == {"deleted": "t1b"}
assert c.delete("/items/t1b").status_code == 404
assert c.delete("/items/t1c").json() == {"deleted": "t1c"}

items = c.get("/items").json()
assert len(items) == 1 and items[0]["heights"] == [[0.1]] and "_id" not in items[0], items
assert all("photo" not in i for i in items), "GET /items must never return the stored photo"
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

# --- delete ----------------------------------------------------------------------
assert c.delete("/items/t4").json() == {"deleted": "t4"}
assert c.get(f"/suitcases/{sc['id']}/plan").status_code == 404, "deleting an item drops the now-stale plan"
assert c.delete(f"/suitcases/{sc['id']}").json() == {"deleted": sc["id"]}
assert c.get(f"/suitcases/{sc['id']}").status_code == 404 and c.get("/items", params={"suitcaseId": sc["id"]}).json() == []
assert c.delete(f"/suitcases/{sc['id']}").status_code == 404
assert c.get("/suitcases").json() == [{k: v for k, v in empty.items()}], "only the untouched suitcase remains"
assert main.db.users.find_one({"_id": "u1"})["email"] == "u1@example.com", "current_user must upsert a users doc"

# --- auth: open mode (no Auth0 tenant configured) lets the phone write with no token ------
del main.app.dependency_overrides[auth.require_auth]  # exercise the real dependency: no Authorization header
assert not os.environ.get("AUTH0_DOMAIN"), "run the check without an Auth0 tenant in the environment"
r = c.post("/suitcases", json={"name": "open", "dimensions": [1, 1, 1]})
assert r.status_code == 200 and c.delete(f"/suitcases/{r.json()['id']}").status_code == 200, r.text
# --- auth: configured tenant, no token / wrong owner ---------------------------------------
with patch.dict(os.environ, {"AUTH0_DOMAIN": "test-tenant.example.auth0.com", "AUTH0_AUDIENCE": "test-audience"}):
    assert c.post("/suitcases", json={"name": "x", "dimensions": [1, 1, 1]}).status_code == 401
    assert c.delete(f"/suitcases/{empty['id']}").status_code == 401
main.app.dependency_overrides[auth.require_auth] = lambda: {"sub": "u1", "email": "u1@example.com"}

main.app.dependency_overrides[auth.require_auth] = lambda: {"sub": "u2"}  # a different, authenticated user
assert c.delete(f"/suitcases/{empty['id']}").status_code == 403, "u2 must not delete u1's suitcase"
assert c.post(f"/suitcases/{empty['id']}/plan").status_code == 403
main.app.dependency_overrides[auth.require_auth] = lambda: {"sub": "u1", "email": "u1@example.com"}
assert c.delete(f"/suitcases/{empty['id']}").json() == {"deleted": empty["id"]}, "u1 (the owner) may delete it"

main.db.items.drop(); main.db.suitcases.drop(); main.db.plans.drop(); main.db.users.drop()
del main.app.dependency_overrides[auth.require_auth]
print("server ok")

# --- auth.py: real JWT verification, no live Auth0 tenant available ---------------
import base64
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt as jose_jwt

DOMAIN, AUDIENCE, KID = "test-tenant.example.auth0.com", "test-audience", "test-kid"
key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def b64url_uint(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


numbers = key.public_key().public_numbers()
jwks_key = {"kty": "RSA", "kid": KID, "use": "sig", "alg": "RS256", "n": b64url_uint(numbers.n), "e": b64url_uint(numbers.e)}


def make_token(**overrides) -> str:
    claims = {"sub": "auth0|abc123", "email": "jwt@example.com", "aud": AUDIENCE, "iss": f"https://{DOMAIN}/", "exp": time.time() + 3600} | overrides
    return jose_jwt.encode(claims, pem, algorithm="RS256", headers={"kid": KID})


with patch.object(auth, "_jwks", return_value=[jwks_key]), patch.dict(os.environ, {"AUTH0_DOMAIN": DOMAIN, "AUTH0_AUDIENCE": AUDIENCE}):
    claims = auth.verify_token(make_token())
    assert claims["sub"] == "auth0|abc123" and claims["email"] == "jwt@example.com", claims

    for bad_token, why in [
        (make_token(exp=time.time() - 10), "expired"),
        (make_token(aud="someone-elses-api"), "wrong audience"),
        (make_token(iss="https://not-our-tenant.example.auth0.com/"), "wrong issuer"),
        (make_token() + "tampered", "corrupted signature"),
    ]:
        try:
            auth.verify_token(bad_token)
            assert False, f"a {why} token must be rejected"
        except jose_jwt.JWTError:
            pass

print("auth ok")
