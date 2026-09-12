"""Contract tests for the FastAPI server (server/main.py) against an in-memory Mongo
(mongomock), exercising the app the way the iOS app does: the exact multipart shape from
`Spike/API.swift` and the JSON keys the `ScannedItem` Codable struct in `Spike/Geometry.swift`
sends/decodes, and the plan document against `packing-core/CLAUDE.md`'s contract.

`server/check.py` already covers upload/label/patch/plan/auth happy- and sad-paths end to
end; this file only adds what it does not: the Swift-shaped multipart fields, the exact
response key set a `ScannedItem` decode needs, replace-not-duplicate on re-upload, the
10MB+1 image 413, `GET /items?suitcaseId=` isolation, delete-invalidates-plan, an unknown
PATCH field being ignored rather than 500, and a unicode label round-trip.

Run (pytest is not in the server uv env, so plain unittest):
    cd server && uv run python -c "import sys; sys.path.insert(0, '../tests'); \
import unittest, test_server_contract; unittest.main(module=test_server_contract)"
"""
import json
import math
import os
import unittest
from unittest.mock import patch

try:  # server deps live in server/.venv, not the root python3 that runs `unittest discover`
    import mongomock
    import pymongo
    from fastapi.testclient import TestClient
except ImportError as exc:
    raise unittest.SkipTest(f"server test deps missing ({exc}); run via the command in the docstring")

os.environ.setdefault("MONGO_DB", "suitcase_contract_test")
try:
    with patch.object(pymongo, "MongoClient", mongomock.MongoClient):
        import main
    import auth
except ImportError as exc:
    raise unittest.SkipTest(f"run from server/ (see docstring): {exc}")

# The six permutation strings AxisRotation.swift decodes (packing-core CLAUDE.md).
ROTATIONS = {"XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX"}

# Every key `ScannedItem` (Spike/Geometry.swift) can decode from a response. All but the
# first four are optional on the Swift side, but the server always fills them in.
SCANNED_ITEM_KEYS = {
    "id", "suitcaseId", "dimensions", "cellSize", "heights", "label", "labelSource",
    "labelStatus", "description", "mass", "keepUpright", "rigidity", "rigiditySource",
    "compressibility", "compressibilitySource", "createdAt",
}

IMG = {"image": ("o.jpg", b"\xff\xd8fake", "image/jpeg")}


def scanned_item_json(item_id: str, suitcase_id: str) -> str:
    """The multipart "item" field body exactly as Swift's `JSONEncoder().encode(item)` would
    produce it: only the non-optional `ScannedItem` fields are present, since `JSONEncoder`
    omits `nil` optionals (label, mass, etc. are unset until the server fills them in)."""
    return json.dumps({
        "id": item_id, "suitcaseId": suitcase_id,
        "dimensions": [0.2, 0.1, 0.1], "cellSize": 0.01, "heights": [[0.1]],
    })


class ServerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        main.app.dependency_overrides[auth.require_auth] = lambda: {"sub": "u1", "email": "u1@example.com"}

    @classmethod
    def tearDownClass(cls):
        del main.app.dependency_overrides[auth.require_auth]

    def setUp(self):
        main.db.items.drop()
        main.db.suitcases.drop()
        main.db.plans.drop()

    def make_suitcase(self, name="carry-on"):
        return self.client.post("/suitcases", json={"name": name, "dimensions": [0.55, 0.22, 0.35]}).json()

    def upload(self, item_id, suitcase_id):
        return self.client.post("/items", data={"item": scanned_item_json(item_id, suitcase_id)}, files=IMG)

    # --- (1) Swift multipart shape / ScannedItem key contract -----------------------------

    def test_upload_multipart_fields_and_response_keys(self):
        """Field names "item"/"image" (API.swift's `upload`) must be what the server accepts,
        and the response must carry every key `ScannedItem` (Geometry.swift) decodes."""
        sc = self.make_suitcase()
        r = self.upload("i1", sc["id"])
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(SCANNED_ITEM_KEYS.issubset(body.keys()), body.keys() - SCANNED_ITEM_KEYS)
        self.assertEqual(body["id"], "i1")
        self.assertEqual(body["suitcaseId"], sc["id"])

    # --- (2) plan response vs packing-core/CLAUDE.md's PackingPlan contract ---------------
    def test_nested_placement_carries_host_and_cavity(self):
        """The solver's `nested_in` record becomes `nestedIn: {itemId, cavity}` (packing-core/CLAUDE.md);
        a record missing its cavity box is emitted as null, never as a bare host id."""
        import app_plan
        sc = self.make_suitcase()
        self.upload("bowl", sc["id"])
        self.upload("cup", sc["id"])
        solver = self.client.post(f"/suitcases/{sc['id']}/plan").json()["solver"]
        by_id = {p["item_id"]: p for p in solver["placements"]}
        self.assertEqual(set(by_id), {"bowl", "cup"})
        host = by_id["bowl"]
        by_id["cup"]["nested_in"] = {"item_id": "bowl", "position": host["position"], "dims": host["dims"]}
        items = {i["id"]: i for i in self.client.get("/items", params={"suitcaseId": sc["id"]}).json()}
        p = app_plan.to_app_plan(solver, {"_id": sc["id"], "name": "s", "dimensions": sc["dimensions"]}, items)
        self.check_placements(p, {z["id"] for z in p["container"]["zones"]})
        nested = {pl["itemId"]: pl["nestedIn"] for pl in p["placements"]}
        self.assertIsNone(nested["bowl"])
        self.assertEqual(nested["cup"]["itemId"], "bowl")
        cav = nested["cup"]["cavity"]
        self.assertEqual(cav["position"], {"x": host["position"][0], "y": host["position"][2], "z": host["position"][1]})
        # without the cavity box the record is worthless to a strict consumer: null, not a bare id
        by_id["cup"]["nested_in"] = {"item_id": "bowl"}
        p2 = app_plan.to_app_plan(solver, {"_id": sc["id"], "name": "s", "dimensions": sc["dimensions"]}, items)
        self.assertIsNone({pl["itemId"]: pl["nestedIn"] for pl in p2["placements"]}["cup"])


    def test_plan_matches_packing_plan_contract(self):
        sc = self.make_suitcase()
        self.upload("i1", sc["id"])
        plan_doc = self.client.post(f"/suitcases/{sc['id']}/plan").json()
        p = plan_doc["plan"]

        self.assertEqual(p["units"], "meters")
        self.assertIn("version", p)
        container = p["container"]
        for key in ("id", "label", "dimensions", "zones"):
            self.assertIn(key, container)
        self.assertEqual(set(container["dimensions"]), {"x", "y", "z"})
        zone_ids = {z["id"] for z in container["zones"]}
        for zone in container["zones"]:
            for key in ("id", "label", "origin", "size"):
                self.assertIn(key, zone)
            self.assertEqual(set(zone["origin"]), {"x", "y", "z"})
            self.assertEqual(set(zone["size"]), {"x", "y", "z"})

        self.check_placements(p, zone_ids)

    def check_placements(self, p: dict, zone_ids: set) -> None:
        steps = sorted(pl["step"] for pl in p["placements"])
        self.assertEqual(steps, list(range(1, len(p["placements"]) + 1)))
        for pl in p["placements"]:
            for key in ("step", "itemId", "label", "zone", "position", "size", "rotation", "note"):
                self.assertIn(key, pl)
            self.assertIn(pl["rotation"], ROTATIONS)
            self.assertIn(pl["zone"], zone_ids)
            self.assertEqual(set(pl["position"]), {"x", "y", "z"})
            self.assertEqual(set(pl["size"]), {"x", "y", "z"})
            # nesting is advisory but must never dangle: a host must be a placement in this plan,
            # and a cavity box only makes sense with a host
            self.assertIn("nestedIn", pl)
            if pl["nestedIn"] is not None:  # {"itemId": host, "cavity": {position, size}} per packing-core/CLAUDE.md
                self.assertEqual(set(pl["nestedIn"]), {"itemId", "cavity"})
                self.assertIn(pl["nestedIn"]["itemId"], {q["itemId"] for q in p["placements"]})
                self.assertNotEqual(pl["nestedIn"]["itemId"], pl["itemId"])
                self.assertEqual(set(pl["nestedIn"]["cavity"]), {"position", "size"})
                for box in pl["nestedIn"]["cavity"].values():
                    self.assertEqual(set(box), {"x", "y", "z"})

    # --- (3) re-uploading the same item id replaces, not duplicates -----------------------

    def test_reupload_same_item_id_replaces(self):
        sc = self.make_suitcase()
        self.upload("dup", sc["id"])
        r = self.client.patch("/items/dup", json={"label": "first label"})
        self.assertEqual(r.json()["label"], "first label")
        r = self.upload("dup", sc["id"])  # re-scan the same item
        self.assertEqual(r.status_code, 200, r.text)
        items = self.client.get("/items", params={"suitcaseId": sc["id"]}).json()
        self.assertEqual([i["id"] for i in items], ["dup"], "must not duplicate")
        # a fresh scan resets a user's earlier PATCH, since it's a brand new upload
        self.assertEqual(self.client.get("/items/dup").json()["labelSource"], "auto")

    # --- (4) an image over MAX_IMAGE_BYTES is rejected -------------------------------------

    def test_oversized_image_rejected_with_413(self):
        sc = self.make_suitcase()
        big = {"image": ("o.jpg", b"\x00" * (main.MAX_IMAGE_BYTES + 1), "image/jpeg")}
        r = self.client.post("/items", data={"item": scanned_item_json("big", sc["id"])}, files=big)
        self.assertEqual(r.status_code, 413, r.text)
        self.assertEqual(self.client.get("/items/big").status_code, 404, "an oversized upload must not be stored")

    # --- (5) GET /items?suitcaseId= isolates suitcases --------------------------------------

    def test_items_isolated_by_suitcase_id(self):
        a, b = self.make_suitcase("a"), self.make_suitcase("b")
        self.upload("a1", a["id"])
        self.upload("b1", b["id"])
        self.assertEqual([i["id"] for i in self.client.get("/items", params={"suitcaseId": a["id"]}).json()], ["a1"])
        self.assertEqual([i["id"] for i in self.client.get("/items", params={"suitcaseId": b["id"]}).json()], ["b1"])

    # --- (6) DELETE /items/{id} invalidates the stored plan ---------------------------------

    def test_delete_item_invalidates_plan(self):
        sc = self.make_suitcase()
        self.upload("i1", sc["id"])
        self.assertEqual(self.client.post(f"/suitcases/{sc['id']}/plan").status_code, 200)
        self.assertEqual(self.client.get(f"/suitcases/{sc['id']}/plan").status_code, 200)
        self.assertEqual(self.client.delete("/items/i1").json(), {"deleted": "i1"})
        self.assertEqual(self.client.get(f"/suitcases/{sc['id']}/plan").status_code, 404)

    # --- (7) an unknown PATCH field is ignored, not a 500 ------------------------------------

    def test_patch_unknown_field_ignored(self):
        sc = self.make_suitcase()
        self.upload("i1", sc["id"])
        r = self.client.patch("/items/i1", json={"label": "known", "notAField": "surprise"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["label"], "known")
        self.assertNotIn("notAField", r.json())

    # --- (8) a unicode label survives a round trip -------------------------------------------

    def test_unicode_label_round_trip(self):
        sc = self.make_suitcase()
        self.upload("i1", sc["id"])
        label = "スーツケース 🧳 café"
        r = self.client.patch("/items/i1", json={"label": label})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["label"], label)
        self.assertEqual(self.client.get("/items/i1").json()["label"], label)

    # --- (9) moving an item between bags ------------------------------------------------------

    def stored_plan(self, bag_id):
        """Stand in a plan document for `bag_id` so a move can be seen to invalidate it."""
        main.db.plans.replace_one({"_id": bag_id}, {"_id": bag_id, "owner_id": "u1", "plan": {}}, upsert=True)

    def test_move_between_bags_drops_both_plans(self):
        a, b = self.make_suitcase("a"), self.make_suitcase("b")
        self.upload("i1", a["id"])
        self.stored_plan(a["id"])
        self.stored_plan(b["id"])
        r = self.client.patch("/items/i1", json={"suitcaseId": b["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["suitcaseId"], b["id"])
        # Neither bag's stored plan still matches its contents.
        self.assertIsNone(main.db.plans.find_one({"_id": a["id"]}))
        self.assertIsNone(main.db.plans.find_one({"_id": b["id"]}))
        # And it moved for real: it is in b's items and gone from a's.
        self.assertEqual([i["id"] for i in self.client.get(f"/items?suitcaseId={b['id']}").json()], ["i1"])
        self.assertEqual(self.client.get(f"/items?suitcaseId={a['id']}").json(), [])

    def test_move_out_of_every_bag_keeps_it_in_inventory(self):
        a = self.make_suitcase("a")
        self.upload("i1", a["id"])
        self.stored_plan(a["id"])
        r = self.client.patch("/items/i1", json={"suitcaseId": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["suitcaseId"])
        self.assertIsNone(main.db.plans.find_one({"_id": a["id"]}))
        self.assertEqual([i["id"] for i in self.client.get("/inventory").json()], ["i1"])

    def test_move_to_a_bag_that_does_not_exist_is_refused(self):
        a = self.make_suitcase("a")
        self.upload("i1", a["id"])
        self.stored_plan(a["id"])
        r = self.client.patch("/items/i1", json={"suitcaseId": "nope"})
        self.assertEqual(r.status_code, 404, r.text)
        # Refused means nothing moved and nothing was invalidated.
        self.assertEqual(self.client.get("/items/i1").json()["suitcaseId"], a["id"])
        self.assertIsNotNone(main.db.plans.find_one({"_id": a["id"]}))

    def test_move_into_someone_elses_bag_is_refused(self):
        a = self.make_suitcase("a")
        self.upload("i1", a["id"])
        main.db.suitcases.insert_one({"_id": "theirs", "owner_id": "u2", "name": "theirs",
                                      "dimensions": [0.5, 0.2, 0.3]})
        r = self.client.patch("/items/i1", json={"suitcaseId": "theirs"})
        # 403, not 404: the bag exists, it just is not yours.
        self.assertEqual(r.status_code, 403, r.text)
        self.assertEqual(self.client.get("/items/i1").json()["suitcaseId"], a["id"])

    def test_move_to_the_bag_it_is_already_in_changes_nothing(self):
        a = self.make_suitcase("a")
        self.upload("i1", a["id"])
        self.stored_plan(a["id"])
        r = self.client.patch("/items/i1", json={"suitcaseId": a["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["suitcaseId"], a["id"])
        # A move that moves nothing must not throw away a plan that still matches.
        self.assertIsNotNone(main.db.plans.find_one({"_id": a["id"]}))

    def test_patching_an_item_that_does_not_exist_is_404(self):
        self.assertEqual(self.client.patch("/items/ghost", json={"suitcaseId": None}).status_code, 404)

    def test_move_survives_a_round_trip_through_inventory(self):
        a, b = self.make_suitcase("a"), self.make_suitcase("b")
        self.upload("i1", a["id"])
        self.client.patch("/items/i1", json={"suitcaseId": b["id"]})
        self.client.patch("/items/i1", json={"suitcaseId": None})
        self.client.patch("/items/i1", json={"suitcaseId": a["id"]})
        self.assertEqual(self.client.get("/items/i1").json()["suitcaseId"], a["id"])
        self.assertEqual(len(self.client.get("/inventory").json()), 1)

    # --- (10) the labelling model the app asks for ---------------------------------------------

    def test_upload_rejects_an_unknown_model(self):
        sc = self.make_suitcase()
        r = self.client.post("/items", data={"item": scanned_item_json("i1", sc["id"]), "model": "gpt"}, files=IMG)
        self.assertEqual(r.status_code, 422, r.text)

    def test_upload_records_the_model_it_was_asked_for(self):
        sc = self.make_suitcase()
        r = self.client.post("/items", data={"item": scanned_item_json("i1", sc["id"]), "model": "claude"}, files=IMG)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(main.db.items.find_one({"_id": "i1"})["labelModel"], "claude")

    def test_upload_defaults_to_both_models(self):
        sc = self.make_suitcase()
        self.upload("i1", sc["id"])
        self.assertEqual(main.db.items.find_one({"_id": "i1"})["labelModel"], "both")

    def test_detect_asks_only_the_model_it_was_told_to(self):
        with patch.dict(os.environ, {"XAI_API_KEY": "x", "ANTHROPIC_API_KEY": "c"}, clear=False):
            with patch.object(main, "_grok_detect", return_value={"label": "grok answer"}) as grok, \
                 patch.object(main, "_claude_detect", return_value={"label": "claude answer"}) as claude:
                self.assertEqual(main.detect(b"jpeg", "grok")["label"], "grok answer")
                self.assertEqual(grok.call_count, 1)
                self.assertEqual(claude.call_count, 0)
                self.assertEqual(main.detect(b"jpeg", "claude")["label"], "claude answer")
                self.assertEqual(claude.call_count, 1)
                # "both" asks each and, on a disagreement, trusts Claude.
                self.assertEqual(main.detect(b"jpeg", "both")["label"], "claude answer")
                self.assertEqual(grok.call_count, 2)


if __name__ == "__main__":
    unittest.main()
