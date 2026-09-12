"""Tests for physics/incremental.py's PlacementValidator.

Strategy: place each fixture scene's objects one by one, bottom-Y-first (so
supporters are always committed before whatever rests on them), and check
that the per-object results match what validate_layout finds for the whole
scene -- same vocabulary, same violation on the same object(s).
"""
import json
import unittest

from physics.geometry import obb_from, obb_vertices
from physics.incremental import PlacementValidator
from physics.schema import Constraints, Container, Object, Scene
from physics.validator import validate_layout
from tests import fixtures


def _bottom_y(obj: Object) -> float:
    return float(obb_vertices(obb_from(obj))[:, 1].min())


def _place_all(scene: Scene):
    """Place every object in `scene`, sorted bottom-Y-first (stable, so ties
    keep the fixture's original relative order). Returns (validator, dict of
    id -> place() result)."""
    pv = PlacementValidator(scene.container)
    results = {}
    for obj in sorted(scene.objects, key=_bottom_y):
        results[obj.id] = pv.place(obj)
    return pv, results


class TestFixtureEquivalence(unittest.TestCase):
    def test_valid_packed_scene_all_accepted(self):
        pv, results = _place_all(fixtures.valid_packed_scene())
        for oid, r in results.items():
            self.assertTrue(r["valid"], f"{oid}: {r['violations']}")
        self.assertTrue(validate_layout(pv.to_scene())["valid"])

    def test_collision_rejects_second_of_colliding_pair(self):
        pv, results = _place_all(fixtures.scene_with_collision())
        # laptop ties with shoe/headphones_case/toiletry_bag at bottom-y=0;
        # fixture order puts laptop before shoe among the tied group, so
        # laptop commits first and shoe is the one that collides with it.
        self.assertTrue(results["laptop"]["valid"])
        self.assertFalse(results["shoe"]["valid"])
        coll = next(v for v in results["shoe"]["violations"] if v["type"] == "OBJECT_COLLISION")
        self.assertEqual(coll["objects"], ["laptop", "shoe"])
        self.assertAlmostEqual(coll["penetration_depth_m"], 0.02, places=6)
        self.assertNotIn("shoe", pv.placed_ids)

    def test_wall_penetration_rejects_laptop(self):
        pv, results = _place_all(fixtures.scene_with_wall_penetration())
        self.assertFalse(results["laptop"]["valid"])
        cp = next(v for v in results["laptop"]["violations"] if v["type"] == "CONTAINER_PENETRATION")
        self.assertEqual(cp["violated_walls"], ["-x"])
        self.assertNotIn("laptop", pv.placed_ids)

    def test_floating_object_rejected(self):
        pv, results = _place_all(fixtures.scene_with_floating_object())
        self.assertFalse(results["charger"]["valid"])
        types = [v["type"] for v in results["charger"]["violations"]]
        self.assertIn("UNSUPPORTED_OBJECT", types)

    def test_stacked_objects_both_accepted_second_has_no_warnings(self):
        pv, results = _place_all(fixtures.scene_stacked_objects())
        for oid, r in results.items():
            self.assertTrue(r["valid"], f"{oid}: {r['violations']}")
        # toiletry_bag (bottom-y=0) commits before headphones_case (bottom-y=0.08).
        self.assertEqual(pv.placed_ids, ["toiletry_bag", "headphones_case"])
        self.assertEqual(results["headphones_case"]["warnings"], [])

    def test_precarious_balance_accepted_with_unstable_warning(self):
        pv, results = _place_all(fixtures.scene_with_precarious_balance())
        self.assertTrue(results["camera"]["valid"])
        types = [w["type"] for w in results["camera"]["warnings"]]
        self.assertIn("UNSTABLE_STACK", types)

    def test_soft_item_compression_warns_not_violates(self):
        pv, results = _place_all(fixtures.scene_with_soft_item_compression())
        for oid, r in results.items():
            self.assertTrue(r["valid"], f"{oid}: {r['violations']}")
        # clothes_bag and toiletry_bag both have bottom-y=0; fixture order
        # puts clothes_bag first, so toiletry_bag is the second placement.
        self.assertEqual(pv.placed_ids, ["clothes_bag", "toiletry_bag"])
        second = results["toiletry_bag"]
        self.assertEqual(second["violations"], [])
        warn_types = [w["type"] for w in second["warnings"]]
        self.assertIn("SOFT_COMPRESSION", warn_types)


class TestFragileOverload(unittest.TestCase):
    def test_shoe_on_fragile_laptop_flags_laptop_not_shoe_geometry(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0), position=(0.0, 0.0, 0.0))
        pv = PlacementValidator(container)
        laptop = Object(
            id="laptop",
            dimensions=(0.4, 0.02, 0.3),
            position=(0.0, -0.49, 0.0),
            mass_kg=1.3,
            constraints=Constraints(cannot_support_weight=True),
        )
        self.assertTrue(pv.place(laptop)["valid"])

        # flush on laptop's top (y=-0.48), footprint inside laptop's.
        shoe = Object(
            id="shoe", dimensions=(0.2, 0.1, 0.15), position=(0.0, -0.43, 0.0), mass_kg=0.3
        )
        result = pv.try_place(shoe)
        overload = next(
            v for v in result["violations"] if v["type"] == "FRAGILE_OBJECT_OVERLOADED"
        )
        self.assertEqual(overload["object"], "laptop")
        self.assertEqual(overload["supported_weight_kg"], shoe.mass_kg)


class TestApiContract(unittest.TestCase):
    def test_try_place_does_not_change_placed_ids(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        pv = PlacementValidator(container)
        a = Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.0, -0.4, 0.0))
        pv.try_place(a)
        self.assertEqual(pv.placed_ids, [])

    def test_place_force_commits_invalid_object(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        pv = PlacementValidator(container)
        a = Object(id="a", dimensions=(0.4, 0.2, 0.4), position=(0.0, -0.4, 0.0))
        b = Object(id="b", dimensions=(0.4, 0.2, 0.4), position=(0.1, -0.4, 0.0))  # overlaps a
        self.assertTrue(pv.place(a)["valid"])
        result = pv.place(b, force=True)
        self.assertFalse(result["valid"])
        self.assertIn("b", pv.placed_ids)

    def test_remove_then_replace(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        pv = PlacementValidator(container)
        a = Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.0, -0.4, 0.0))
        pv.place(a)
        pv.remove("a")
        self.assertEqual(pv.placed_ids, [])
        result = pv.place(a)
        self.assertTrue(result["valid"])
        self.assertEqual(pv.placed_ids, ["a"])

    def test_duplicate_id_is_malformed_no_raise(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        pv = PlacementValidator(container)
        a = Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.0, -0.4, 0.0))
        pv.place(a)
        dup = Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.5, -0.4, 0.0))
        result = pv.try_place(dup)
        self.assertFalse(result["valid"])
        self.assertEqual(result["violations"][0]["type"], "MALFORMED_GEOMETRY")

    def test_nan_dims_malformed_does_not_raise(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        pv = PlacementValidator(container)
        a = Object(id="a", dimensions=(0.2, float("nan"), 0.2), position=(0.0, -0.4, 0.0))
        result = pv.try_place(a)
        self.assertFalse(result["valid"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(len(result["violations"]), 1)
        self.assertEqual(result["violations"][0]["type"], "MALFORMED_GEOMETRY")
        self.assertEqual(result["warnings"], [])

    def test_determinism(self):
        scene = fixtures.valid_packed_scene()
        pv = PlacementValidator(scene.container)
        ordered = sorted(scene.objects, key=_bottom_y)
        for obj in ordered[:-1]:
            pv.place(obj)
        last = ordered[-1]
        r1 = pv.try_place(last)
        r2 = pv.try_place(last)
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(r1, sort_keys=False), json.dumps(r2, sort_keys=False))


class TestIncrementalMetrics(unittest.TestCase):
    def test_no_committed_objects_gap_is_none(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        pv = PlacementValidator(container)
        a = Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.0, 0.0, 0.0))
        metrics = pv.incremental_metrics(a)
        self.assertIsNone(metrics["nearest_neighbor_gap_m"])
        self.assertGreater(metrics["wall_clearance_m"], 0.0)

    def test_overlapping_gap_is_zero_and_clearance_shrinks_with_distance(self):
        container = Container(id="c", dimensions=(1.0, 1.0, 1.0))
        pv = PlacementValidator(container)
        a = Object(id="a", dimensions=(0.2, 0.2, 0.2), position=(0.0, 0.0, 0.0))
        pv.place(a, force=True)  # floating at scene center; fine, only AABB gaps are under test

        overlapping = Object(id="b", dimensions=(0.2, 0.2, 0.2), position=(0.05, 0.0, 0.0))
        self.assertEqual(pv.incremental_metrics(overlapping)["nearest_neighbor_gap_m"], 0.0)

        far = Object(id="c2", dimensions=(0.2, 0.2, 0.2), position=(0.3, 0.0, 0.0))
        gap = pv.incremental_metrics(far)["nearest_neighbor_gap_m"]
        self.assertAlmostEqual(gap, 0.1, places=6)


if __name__ == "__main__":
    unittest.main()
