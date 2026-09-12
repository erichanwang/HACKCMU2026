"""Sanity checks for tests/fixtures.py -- catches fixture-authoring bugs
(duplicate ids, non-finite numbers, bad quaternions) without duplicating the
real collision/containment/support logic those fixtures exist to feed.
"""
import math
import unittest

from physics.io import scene_from_dict, scene_to_dict
from physics.schema import Scene
from physics.scene_geometry import precompute
from tests import fixtures

FIXTURE_FUNCS = [
    fixtures.valid_packed_scene,
    fixtures.scene_with_collision,
    fixtures.scene_with_wall_penetration,
    fixtures.scene_with_floating_object,
    fixtures.scene_with_precarious_balance,
    fixtures.scene_nearly_full,
    fixtures.scene_object_touching_wall,
    fixtures.scene_rotated_object_in_corner,
    fixtures.scene_stacked_objects,
    fixtures.scene_oversized_object,
    fixtures.scene_with_soft_item_compression,
    fixtures.scene_scanned_hulls,
    fixtures.scene_hull_clears_box_would_not,
    fixtures.scene_scanned_footprint_out_of_dims,
]


def _is_finite_vec3(v) -> bool:
    return len(v) == 3 and all(isinstance(c, (int, float)) and math.isfinite(c) for c in v)


class TestFixtureSanity(unittest.TestCase):
    def test_all_fixtures_return_scene_with_container_and_objects(self):
        for fn in FIXTURE_FUNCS:
            with self.subTest(fixture=fn.__name__):
                scene = fn()
                self.assertIsInstance(scene, Scene)
                self.assertIsNotNone(scene.container)
                self.assertTrue(len(scene.objects) > 0, "fixture has no objects")

    def test_object_ids_unique_within_each_fixture(self):
        for fn in FIXTURE_FUNCS:
            with self.subTest(fixture=fn.__name__):
                scene = fn()
                ids = [o.id for o in scene.objects]
                self.assertEqual(len(ids), len(set(ids)), f"duplicate ids in {ids}")

    def test_dimensions_finite_and_positive(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            entities = [scene.container] + list(scene.objects)
            for e in entities:
                with self.subTest(fixture=fn.__name__, id=getattr(e, "id", "container")):
                    self.assertTrue(_is_finite_vec3(e.dimensions))
                    self.assertTrue(all(d > 0 for d in e.dimensions))

    def test_positions_finite(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            entities = [scene.container] + list(scene.objects)
            for e in entities:
                with self.subTest(fixture=fn.__name__, id=getattr(e, "id", "container")):
                    self.assertTrue(_is_finite_vec3(e.position))

    def test_quaternions_are_unit_length(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            entities = [scene.container] + list(scene.objects)
            for e in entities:
                with self.subTest(fixture=fn.__name__, id=getattr(e, "id", "container")):
                    x, y, z, w = e.rotation
                    norm = math.sqrt(x * x + y * y + z * z + w * w)
                    self.assertAlmostEqual(norm, 1.0, places=6)

    def test_mass_positive_for_objects(self):
        for fn in FIXTURE_FUNCS:
            scene = fn()
            for o in scene.objects:
                with self.subTest(fixture=fn.__name__, id=o.id):
                    self.assertGreater(o.mass_kg, 0.0)


    def test_soft_item_compression_is_warning_not_violation(self):
        from physics.validator import validate_layout

        result = validate_layout(fixtures.scene_with_soft_item_compression())
        self.assertTrue(result["valid"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertEqual(result["warnings"][0]["type"], "SOFT_COMPRESSION")


class TestScannedFootprintFixtures(unittest.TestCase):
    def test_scene_scanned_hulls_round_trips_footprints(self):
        scene = fixtures.scene_scanned_hulls()
        rebuilt = scene_from_dict(scene_to_dict(scene))
        self.assertEqual(rebuilt, scene)
        for oid in ("shoe", "toiletry_bag", "camera"):
            obj = next(o for o in rebuilt.objects if o.id == oid)
            self.assertIsNotNone(obj.footprint)

    def test_scene_scanned_hulls_is_valid(self):
        from physics.validator import validate_layout

        result = validate_layout(fixtures.scene_scanned_hulls())
        self.assertTrue(result["valid"], result["violations"])

    def test_scene_hull_clears_box_would_not_boxes_overlap_hulls_disjoint(self):
        """precompute must succeed (footprints are well-formed); the boxes
        (AABBs) overlap by ~1cm^2 at a corner while the footprint polygons
        themselves are disjoint (checked here with a small separating-axis
        test) -- see the fixture's docstring for the arithmetic. Whether
        `validate_layout` treats this pair as clear is for the collision
        tests to assert, not this one.
        """
        scene = fixtures.scene_hull_clears_box_would_not()
        geom = precompute(scene)  # must not raise
        lo, hi = geom.aabb_min, geom.aabb_max
        box_overlap = all(min(hi[0][k], hi[1][k]) > max(lo[0][k], lo[1][k]) for k in (0, 2))
        self.assertTrue(box_overlap, "fixture's boxes should overlap in both x and z")

        camera, shoe = scene.objects
        cam_world = [(x + camera.position[0], z + camera.position[2]) for x, z in fixtures.CAMERA_FOOTPRINT]
        shoe_world = [(x + shoe.position[0], z + shoe.position[2]) for x, z in fixtures.SHOE_FOOTPRINT]
        self.assertTrue(_polygons_disjoint(cam_world, shoe_world), "fixture's hulls should be disjoint")

    def test_scanned_footprint_out_of_dims_reports_malformed(self):
        from physics.scene_geometry import MalformedSceneError

        with self.assertRaises(MalformedSceneError) as ctx:
            precompute(fixtures.scene_scanned_footprint_out_of_dims())
        self.assertEqual(ctx.exception.object_id, "shoe")


def _polygons_disjoint(poly1, poly2) -> bool:
    """Separating-axis test for two convex 2D polygons (list of (x, z))."""
    def edges(poly):
        n = len(poly)
        return [(poly[(i + 1) % n][0] - poly[i][0], poly[(i + 1) % n][1] - poly[i][1]) for i in range(n)]

    def project(poly, axis):
        vals = [px * axis[0] + pz * axis[1] for px, pz in poly]
        return min(vals), max(vals)

    for poly in (poly1, poly2):
        for ex, ez in edges(poly):
            length = math.hypot(ex, ez)
            if length < 1e-12:
                continue
            axis = (-ez / length, ex / length)
            min1, max1 = project(poly1, axis)
            min2, max2 = project(poly2, axis)
            if max1 < min2 or max2 < min1:
                return True
    return False


if __name__ == "__main__":
    unittest.main()
