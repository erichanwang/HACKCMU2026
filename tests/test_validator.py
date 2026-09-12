import json
import math
import unittest

from physics.schema import Constraints, Container, Object, Scene
from physics.validator import validate_layout

CONTAINER = Container(id="suitcase", dimensions=(1.0, 1.0, 1.0), position=(0, 0, 0))
# floor world-y = -0.5, ceiling world-y = 0.5, walls at x/z = +-0.5


def obj(
    id_,
    dims,
    position,
    rotation=(0.0, 0.0, 0.0, 1.0),
    mass_kg=1.0,
    constraints=None,
    rigidity="rigid",
    compressibility_k=1.0,
):
    return Object(
        id=id_,
        dimensions=dims,
        position=position,
        rotation=rotation,
        mass_kg=mass_kg,
        constraints=constraints or Constraints(),
        rigidity=rigidity,
        compressibility_k=compressibility_k,
    )


def scene_of(*objects):
    return Scene(container=CONTAINER, objects=list(objects))


class TestValidateLayout(unittest.TestCase):
    def test_clean_scene(self):
        a = obj("a", (0.2, 0.2, 0.2), (0, -0.4, 0))  # flush on floor
        b = obj("b", (0.2, 0.2, 0.2), (0.3, -0.4, 0))  # side by side, no overlap
        result = validate_layout(scene_of(a, b))
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["score"], 1.0, places=6)
        self.assertEqual(result["violations"], [])

    def test_object_collision(self):
        a = obj("a", (0.4, 0.2, 0.4), (0, -0.4, 0))
        b = obj("b", (0.4, 0.2, 0.4), (0.1, -0.4, 0))  # heavily overlapping in x
        result = validate_layout(scene_of(a, b))
        self.assertFalse(result["valid"])
        types = [v["type"] for v in result["violations"]]
        self.assertIn("OBJECT_COLLISION", types)
        coll = next(v for v in result["violations"] if v["type"] == "OBJECT_COLLISION")
        self.assertEqual(coll["objects"], ["a", "b"])
        self.assertGreater(coll["penetration_depth_m"], 0)
        self.assertGreaterEqual(coll["severity"], 0)

    def test_container_penetration(self):
        # half-height 0.4 -> object spans y in [-0.6, 0.2], well past floor at -0.5
        a = obj("a", (0.2, 0.8, 0.2), (0, -0.2, 0))
        result = validate_layout(scene_of(a))
        self.assertFalse(result["valid"])
        types = [v["type"] for v in result["violations"]]
        self.assertIn("CONTAINER_PENETRATION", types)
        cp = next(v for v in result["violations"] if v["type"] == "CONTAINER_PENETRATION")
        self.assertEqual(cp["object"], "a")
        self.assertGreater(cp["penetration_depth_m"], 0)

    def test_floating_object(self):
        a = obj("a", (0.2, 0.2, 0.2), (0, -0.2, 0))  # gap above floor, nothing beneath
        result = validate_layout(scene_of(a))
        self.assertFalse(result["valid"])
        types = [v["type"] for v in result["violations"]]
        self.assertIn("UNSUPPORTED_OBJECT", types)
        u = next(v for v in result["violations"] if v["type"] == "UNSUPPORTED_OBJECT")
        self.assertEqual(u["object"], "a")
        self.assertEqual(u["support_ratio"], 0.0)

    def test_unstable_but_supported_is_warning_not_violation(self):
        base = obj("base", (0.4, 0.2, 0.4), (0, -0.4, 0))  # top rect [-0.2,0.2]^2
        # bottom rect x[0.15,0.35] z[-0.1,0.1]; only x[0.15,0.2] overlaps base -> hangs off edge
        hanger = obj("hanger", (0.2, 0.1, 0.2), (0.25, -0.25, 0))
        result = validate_layout(scene_of(base, hanger))
        v_types = [v["type"] for v in result["violations"]]
        w_types = [w["type"] for w in result["warnings"]]
        self.assertNotIn("UNSTABLE_STACK", v_types)
        self.assertIn("UNSTABLE_STACK", w_types)
        # documented policy: unstable-but-supported does not by itself invalidate the scene
        self.assertTrue(result["valid"])
        stack = next(w for w in result["warnings"] if w["type"] == "UNSTABLE_STACK")
        self.assertEqual(stack["object"], "hanger")
        self.assertLess(stack["stability_margin_m"], 0)

    def test_malformed_geometry_nan_dimensions_does_not_raise(self):
        a = obj("a", (0.2, float("nan"), 0.2), (0, -0.4, 0))
        result = validate_layout(scene_of(a))
        self.assertFalse(result["valid"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(len(result["violations"]), 1)
        self.assertEqual(result["violations"][0]["type"], "MALFORMED_GEOMETRY")
        self.assertEqual(result["warnings"], [])

    def test_duplicate_ids_does_not_raise(self):
        a = obj("dup", (0.2, 0.2, 0.2), (0, -0.4, 0))
        b = obj("dup", (0.2, 0.2, 0.2), (0.3, -0.4, 0))
        result = validate_layout(scene_of(a, b))
        self.assertFalse(result["valid"])
        self.assertEqual(result["score"], 0.0)
        self.assertEqual(result["violations"][0]["type"], "MALFORMED_GEOMETRY")

    def test_determinism(self):
        a = obj("a", (0.4, 0.2, 0.4), (0, -0.4, 0))
        b = obj("b", (0.4, 0.2, 0.4), (0.1, -0.4, 0))  # colliding
        c = obj("c", (0.2, 0.2, 0.2), (0, -0.2, 0))  # floating
        scene = scene_of(a, b, c)
        r1 = validate_layout(scene)
        r2 = validate_layout(scene)
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(r1, sort_keys=False), json.dumps(r2, sort_keys=False))

    def test_rotated_but_valid_scene(self):
        # 45 degree yaw about world Y; object well clear of walls and floor-supported.
        half_angle = math.radians(45.0) / 2.0
        rot = (0.0, math.sin(half_angle), 0.0, math.cos(half_angle))
        a = obj("a", (0.2, 0.2, 0.2), (0, -0.4, 0), rotation=rot)
        result = validate_layout(scene_of(a))
        self.assertTrue(result["valid"])
        self.assertAlmostEqual(result["score"], 1.0, places=6)
        self.assertEqual(result["violations"], [])


class TestCompressibility(unittest.TestCase):
    # Note: for dims (0.4, 0.2, 0.4) at x-offset 0.1, SAT's minimum-overlap
    # (MTV) axis is actually Y (extent 0.2, zero center offset -> overlap
    # 0.2), not X (extent 0.4, overlap 0.3) -- Y wins because its overlap is
    # smaller. So the raw penetration in test_object_collision is 0.2m along
    # Y, and allowance below is computed from each object's own Y extent
    # (0.2m), not its X extent.

    def test_soft_overlap_within_allowance_is_warning_not_violation(self):
        a = obj("a", (0.4, 0.2, 0.4), (0, -0.4, 0), rigidity="soft", compressibility_k=3.0)
        b = obj(
            "b", (0.4, 0.2, 0.4), (0.1, -0.4, 0), rigidity="soft", compressibility_k=3.0
        )  # same raw overlap as test_object_collision (0.2m along y)
        result = validate_layout(scene_of(a, b))
        types = [v["type"] for v in result["violations"]]
        self.assertNotIn("OBJECT_COLLISION", types)
        self.assertTrue(result["valid"])
        w_types = [w["type"] for w in result["warnings"]]
        self.assertIn("SOFT_COMPRESSION", w_types)
        w = next(w for w in result["warnings"] if w["type"] == "SOFT_COMPRESSION")
        self.assertEqual(w["objects"], ["a", "b"])
        self.assertAlmostEqual(w["raw_penetration_depth_m"], 0.2, places=6)
        self.assertAlmostEqual(w["compressed_depth_m"], 0.2, places=6)

    def test_soft_overlap_past_allowance_still_violation_but_reduced_severity(self):
        a_soft = obj("a", (0.4, 0.2, 0.4), (0, -0.4, 0), rigidity="soft", compressibility_k=1.2)
        b_soft = obj("b", (0.4, 0.2, 0.4), (0.1, -0.4, 0), rigidity="soft", compressibility_k=1.2)
        soft_result = validate_layout(scene_of(a_soft, b_soft))
        soft_types = [v["type"] for v in soft_result["violations"]]
        self.assertIn("OBJECT_COLLISION", soft_types)
        soft_coll = next(v for v in soft_result["violations"] if v["type"] == "OBJECT_COLLISION")
        # raw penetration is 0.2m (identical geometry to test_object_collision);
        # allowance only partially absorbs it, so depth used is < raw.
        self.assertLess(soft_coll["penetration_depth_m"], 0.2)
        self.assertGreater(soft_coll["penetration_depth_m"], 0.0)

        a_rigid = obj("a", (0.4, 0.2, 0.4), (0, -0.4, 0))
        b_rigid = obj("b", (0.4, 0.2, 0.4), (0.1, -0.4, 0))
        rigid_result = validate_layout(scene_of(a_rigid, b_rigid))
        rigid_coll = next(
            v for v in rigid_result["violations"] if v["type"] == "OBJECT_COLLISION"
        )
        self.assertLess(soft_coll["severity"], rigid_coll["severity"])

    def test_rigid_pair_unaffected_by_compressibility_wiring(self):
        # identical to test_object_collision -- confirms zero regression.
        a = obj("a", (0.4, 0.2, 0.4), (0, -0.4, 0))
        b = obj("b", (0.4, 0.2, 0.4), (0.1, -0.4, 0))
        result = validate_layout(scene_of(a, b))
        coll = next(v for v in result["violations"] if v["type"] == "OBJECT_COLLISION")
        self.assertAlmostEqual(coll["penetration_depth_m"], 0.2, places=6)
        self.assertEqual(result["warnings"], [])

    def test_soft_object_bulging_past_wall_within_allowance_is_warning(self):
        # half-height 0.25 -> bottom at y=-0.65, floor at y=-0.5: raw penetration 0.15m.
        a = obj(
            "a", (0.2, 0.5, 0.2), (0, -0.4, 0), rigidity="soft", compressibility_k=2.0
        )
        result = validate_layout(scene_of(a))
        types = [v["type"] for v in result["violations"]]
        self.assertNotIn("CONTAINER_PENETRATION", types)
        w_types = [w["type"] for w in result["warnings"]]
        self.assertIn("SOFT_COMPRESSION", w_types)
        w = next(w for w in result["warnings"] if w["type"] == "SOFT_COMPRESSION")
        self.assertEqual(w["object"], "a")
        self.assertAlmostEqual(w["raw_penetration_depth_m"], 0.15, places=6)

    def test_determinism_with_compressibility(self):
        a = obj("a", (0.4, 0.2, 0.4), (0, -0.4, 0), rigidity="soft", compressibility_k=1.2)
        b = obj("b", (0.4, 0.2, 0.4), (0.1, -0.4, 0), rigidity="soft", compressibility_k=1.2)
        scene = scene_of(a, b)
        r1 = validate_layout(scene)
        r2 = validate_layout(scene)
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(r1, sort_keys=False), json.dumps(r2, sort_keys=False))


if __name__ == "__main__":
    unittest.main()
