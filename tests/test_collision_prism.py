"""Tests for the convex-prism narrow phase in physics.collision.

The box-only SAT tests live in tests/test_collision.py; everything here needs a
`footprint` (a LiDAR hull) on at least one object.

Run: python3 -m unittest tests.test_collision_prism -v
"""
from __future__ import annotations

import math
import time
import unittest

import numpy as np

from physics.collision import check_collision, check_prism_pair, collide_scene
from physics.geometry import polygon_area_2d, polygon_centroid_2d
from physics.scene_geometry import precompute
from physics.schema import Container, Object, Scene

# Triangle occupying the x + z <= 0 half of its 0.2 x 0.2 bounding box: the
# (+x, +z) corner of the box is empty. dims stay the bounding box, per schema.
TRIANGLE = [(-0.1, -0.1), (0.1, -0.1), (-0.1, 0.1)]


def _scene(objects, container_dims=(4.0, 4.0, 4.0)):
    return Scene(
        container=Container(id="c", dimensions=container_dims, position=(0.0, 0.0, 0.0)),
        objects=objects,
    )


def _ngon(sides, radius, phase_deg=0.0):
    """Regular polygon footprint, `sides` points on a circle of `radius`."""
    a = np.radians(phase_deg) + np.arange(sides) * 2 * math.pi / sides
    return [(radius * math.cos(t), radius * math.sin(t)) for t in a]


def _bbox_dims(footprint, height):
    """The dimensions a footprint forces: its bounding box, height along y."""
    p = np.asarray(footprint, dtype=float)
    return (
        float(p[:, 0].max() - p[:, 0].min()),
        float(height),
        float(p[:, 1].max() - p[:, 1].min()),
    )


def _quat_y(deg):
    h = math.radians(deg) / 2
    return (0.0, math.sin(h), 0.0, math.cos(h))


def _quat_x(deg):
    h = math.radians(deg) / 2
    return (math.sin(h), 0.0, 0.0, math.cos(h))


class TestHeadlineScanBeatsBox(unittest.TestCase):
    """The demo point: the box envelope says collision, the scan says it fits.

    A box sitting in the triangle prism's empty bounding-box corner. Note the
    prism's AABB is its full bounding box here (its vertices reach every side),
    so the pair survives the broad phase and is decided by the narrow phase.
    """

    def setUp(self):
        self.tri = Object(
            id="tri", dimensions=(0.2, 0.1, 0.2), position=(0.0, 0.0, 0.0), footprint=TRIANGLE
        )
        # x in [0.07, 0.17], z in [0.07, 0.17]: inside the triangle's bbox corner
        # (x + z >= 0.14 > 0 there), outside the triangle itself.
        self.bx = Object(id="bx", dimensions=(0.1, 0.1, 0.1), position=(0.12, 0.0, 0.12))
        self.geom = precompute(_scene([self.tri, self.bx]))

    def test_envelopes_collide(self):
        res = check_collision(self.geom.obbs[0], self.geom.obbs[1])
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 0.03, delta=1e-12)  # 0.1 + 0.05 - 0.12

    def test_prism_does_not(self):
        self.assertEqual(collide_scene(self.geom), [])
        res = check_prism_pair(self.geom, 0, 1)
        self.assertFalse(res.colliding)
        self.assertEqual(res.narrow_phase, "sat_prism")
        self.assertFalse(res.approximate)

    def test_broad_phase_did_not_do_the_work(self):
        """Guard the test's own premise: the AABBs really do overlap."""
        np.testing.assert_allclose(self.geom.aabb_min[0], [-0.1, -0.05, -0.1], atol=1e-15)
        np.testing.assert_allclose(self.geom.aabb_max[0], [0.1, 0.05, 0.1], atol=1e-15)
        self.assertTrue(np.all(self.geom.aabb_max[0] >= self.geom.aabb_min[1]))


class TestFourPointPrismMatchesOBB(unittest.TestCase):
    """A prism whose footprint IS its rectangle must agree with the OBB path."""

    def _pair(self, rot=(0.0, 0.0, 0.0, 1.0)):
        fp = [(-0.1, -0.2), (0.1, -0.2), (0.1, 0.2), (-0.1, 0.2)]
        a = Object(id="a", dimensions=(0.2, 0.3, 0.4), position=(0.0, 0.0, 0.0),
                   rotation=rot, footprint=fp)
        b = Object(id="b", dimensions=(0.2, 0.3, 0.4), position=(0.15, 0.0, 0.0),
                   rotation=rot, footprint=fp)
        return precompute(_scene([a, b]))

    def test_axis_aligned(self):
        geom = self._pair()
        prism = check_prism_pair(geom, 0, 1)
        obb = check_collision(geom.obbs[0], geom.obbs[1])
        self.assertTrue(prism.colliding)
        self.assertTrue(obb.colliding)
        self.assertAlmostEqual(prism.penetration_depth_m, 0.05, delta=1e-12)  # 0.2 - 0.15
        self.assertAlmostEqual(
            prism.penetration_depth_m, obb.penetration_depth_m, delta=1e-12
        )
        np.testing.assert_allclose(prism.axis, obb.axis, atol=1e-12)
        np.testing.assert_allclose(prism.axis, [1, 0, 0], atol=1e-12)

    def test_upside_down_footprint_winding(self):
        """Local y = world -Y reflects the XZ plane, flipping the ring's winding.
        The outward normals must still point outward (else every overlap flips
        sign and the test inverts)."""
        geom = self._pair(rot=(1.0, 0.0, 0.0, 0.0))  # 180 deg about X
        self.assertTrue(geom.yaw_only[0] and geom.yaw_only[1])
        prism = check_prism_pair(geom, 0, 1)
        obb = check_collision(geom.obbs[0], geom.obbs[1])
        self.assertTrue(prism.colliding)
        self.assertAlmostEqual(
            prism.penetration_depth_m, obb.penetration_depth_m, delta=1e-12
        )


class TestOctagonKnownOverlap(unittest.TestCase):
    """Octagon prism (vertices on the axes, so its max x is exactly 0.1) against
    a box whose near face is at x = 0.09: the overlap along X is 0.01, and every
    other candidate axis (the octagon's 8 edge normals at 22.5 + k*45 deg, the
    box's Z normals, world Y) overlaps by much more."""

    def setUp(self):
        oct_fp = _ngon(8, 0.1)
        self.oc = Object(id="oc", dimensions=_bbox_dims(oct_fp, 0.1),
                         position=(0.0, 0.0, 0.0), footprint=oct_fp)
        self.bx = Object(id="bx", dimensions=(0.2, 0.2, 0.4), position=(0.19, 0.0, 0.0))
        self.geom = precompute(_scene([self.oc, self.bx]))

    def test_depth_exact(self):
        res = check_prism_pair(self.geom, 0, 1)
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 0.01, delta=1e-12)
        np.testing.assert_allclose(res.axis, [1, 0, 0], atol=1e-12)

    def test_contact_polygon_is_the_patch(self):
        res = check_prism_pair(self.geom, 0, 1)
        poly = np.asarray(res.contact_polygon, dtype=float)
        self.assertGreaterEqual(len(poly), 3)
        self.assertGreater(polygon_area_2d(poly), 0.0)
        # Every vertex is in both shapes' XZ extents (it is their intersection).
        self.assertTrue(np.all(poly[:, 0] >= 0.09 - 1e-12))
        self.assertTrue(np.all(poly[:, 0] <= 0.1 + 1e-12))
        cx, cz = polygon_centroid_2d(poly)
        self.assertAlmostEqual(res.contact_point[0], cx, delta=1e-12)
        self.assertAlmostEqual(res.contact_point[2], cz, delta=1e-12)
        self.assertAlmostEqual(res.contact_point[1], 0.0, delta=1e-12)  # mid Y overlap


class TestYawedHexagon(unittest.TestCase):
    """45 deg yaw: the footprint is still exact in world XZ, so the empty corners
    of the (rotated) bounding box stay empty."""

    def _geom(self, box_pos, box_dims=(0.03, 0.1, 0.03)):
        hexa = _ngon(6, 0.1)
        obj = Object(id="hex", dimensions=_bbox_dims(hexa, 0.1), position=(0.0, 0.0, 0.0),
                     rotation=_quat_y(45), footprint=hexa)
        bx = Object(id="bx", dimensions=box_dims, position=box_pos)
        return precompute(_scene([obj, bx]))

    def test_colliding_at_a_rotated_vertex(self):
        # local (0.1, 0) -> world (0.1*cos45, -0.1*sin45)
        c = 0.1 / math.sqrt(2.0)
        geom = self._geom((c, 0.0, -c))
        res = check_prism_pair(geom, 0, 1)
        self.assertTrue(res.colliding)
        self.assertEqual(res.narrow_phase, "sat_prism")
        self.assertTrue(check_collision(geom.obbs[0], geom.obbs[1]).colliding)

    def test_clear_at_a_rotated_bbox_corner(self):
        # local bbox corner (0.1, 0.0866) is 0.0433 m outside the hexagon.
        hz = 0.1 * math.sin(math.radians(60))
        s = 1.0 / math.sqrt(2.0)
        geom = self._geom(((0.1 + hz) * s, 0.0, (-0.1 + hz) * s))
        self.assertTrue(check_collision(geom.obbs[0], geom.obbs[1]).colliding)
        self.assertFalse(check_prism_pair(geom, 0, 1).colliding)
        self.assertEqual(collide_scene(geom), [])


class TestVerticalStack(unittest.TestCase):
    def _geom(self, gap):
        fp = _ngon(8, 0.1)
        dims = _bbox_dims(fp, 0.1)
        return precompute(_scene([
            Object(id="lo", dimensions=dims, position=(0.0, 0.0, 0.0), footprint=fp),
            Object(id="hi", dimensions=dims, position=(0.0, 0.1 - gap, 0.0), footprint=fp),
        ]))

    def test_exactly_touching_not_colliding(self):
        geom = self._geom(0.0)  # top ring of `lo` at y = 0.05 == bottom ring of `hi`
        self.assertAlmostEqual(geom.aabb_max[0][1], geom.aabb_min[1][1], delta=1e-15)
        res = check_prism_pair(geom, 0, 1)
        self.assertFalse(res.colliding)
        self.assertEqual(res.penetration_depth_m, 0.0)
        self.assertEqual(collide_scene(geom), [])

    def test_tiny_vertical_overlap_colliding(self):
        res = check_prism_pair(self._geom(1e-4), 0, 1)
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 1e-4, delta=1e-12)
        np.testing.assert_allclose(res.axis, [0, 1, 0], atol=1e-12)  # `hi` is above

    def test_epsilon_swallows_1e7_overlap(self):
        self.assertFalse(check_prism_pair(self._geom(1e-7), 0, 1).colliding)


class TestEpsilonSemantics(unittest.TestCase):
    """Square footprints, so the overlap along X is the MTV exactly (an octagon
    would put the minimum on a 22.5 deg edge normal instead)."""

    def _geom(self, overlap):
        fp = [(-0.1, -0.1), (0.1, -0.1), (0.1, 0.1), (-0.1, 0.1)]
        return precompute(_scene([
            Object(id="a", dimensions=(0.2, 0.1, 0.2), position=(0.0, 0.0, 0.0), footprint=fp),
            Object(id="b", dimensions=(0.2, 0.1, 0.2), position=(0.2 - overlap, 0.0, 0.0),
                   footprint=fp),
        ]))

    def test_1e7_overlap_not_colliding(self):
        self.assertFalse(check_prism_pair(self._geom(1e-7), 0, 1).colliding)
        self.assertEqual(collide_scene(self._geom(1e-7)), [])

    def test_1e4_overlap_colliding(self):
        res = check_prism_pair(self._geom(1e-4), 0, 1)
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 1e-4, delta=1e-12)
        np.testing.assert_allclose(res.axis, [1, 0, 0], atol=1e-12)


class TestTiltedFallback(unittest.TestCase):
    """A pitched prism is no longer a vertical extrusion in world XZ, so the
    footprint cannot be used: fall back to the box envelopes, flagged."""

    def setUp(self):
        self.geom = precompute(_scene([
            Object(id="tri", dimensions=(0.2, 0.1, 0.2), position=(0.0, 0.0, 0.0),
                   rotation=_quat_x(20), footprint=TRIANGLE),
            Object(id="bx", dimensions=(0.1, 0.1, 0.1), position=(0.12, 0.0, 0.12)),
        ]))

    def test_flags_and_verdict(self):
        self.assertFalse(self.geom.yaw_only[0])
        res = check_prism_pair(self.geom, 0, 1)
        envelope = check_collision(self.geom.obbs[0], self.geom.obbs[1])
        self.assertEqual(res.narrow_phase, "sat_obb_envelope")
        self.assertTrue(res.approximate)
        self.assertEqual(res.colliding, envelope.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, envelope.penetration_depth_m, delta=1e-15)
        self.assertIsNone(res.contact_polygon)

    def test_collide_scene_uses_the_fallback(self):
        results = collide_scene(self.geom)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].narrow_phase, "sat_obb_envelope")
        self.assertTrue(results[0].approximate)


class TestCollideSceneRouting(unittest.TestCase):
    """Box-box pairs in a scene that also contains prisms must still take the
    batched OBB path (unchanged results), and ordering stays ascending (i, j)."""

    def test_mixed_scene(self):
        fp = _ngon(8, 0.1)
        dims = _bbox_dims(fp, 0.1)
        geom = precompute(_scene([
            Object(id="b0", dimensions=(0.2, 0.2, 0.2), position=(0.0, 0.0, 0.0)),
            Object(id="b1", dimensions=(0.2, 0.2, 0.2), position=(0.15, 0.0, 0.0)),
            Object(id="p2", dimensions=dims, position=(0.15, 0.0, 0.15), footprint=fp),
        ]))
        results = collide_scene(geom)
        by_pair = {(r.a_id, r.b_id): r for r in results}
        self.assertEqual([(r.a_id, r.b_id) for r in results], sorted(by_pair))
        self.assertEqual(by_pair[("b0", "b1")].narrow_phase, "sat_obb")
        self.assertFalse(by_pair[("b0", "b1")].approximate)
        self.assertIsNone(by_pair[("b0", "b1")].contact_polygon)
        self.assertAlmostEqual(by_pair[("b0", "b1")].penetration_depth_m, 0.05, delta=1e-12)
        self.assertEqual(by_pair[("b1", "p2")].narrow_phase, "sat_prism")


def _random_prism_scene(seed, n_pairs):
    """2*n_pairs yaw-only objects, each randomly a box or a random convex prism
    (hull of 5-9 uniform points in its own bounding rectangle). Pair p is
    objects (2p, 2p+1)."""
    rng = np.random.default_rng(seed)
    objects = []
    for k in range(2 * n_pairs):
        dims = tuple(rng.uniform(0.1, 0.4, 3))
        fp = None
        if k % 2 or rng.random() < 0.5:  # ~75% prisms, but both kinds in every role
            pts = rng.uniform(-1.0, 1.0, (int(rng.integers(5, 10)), 2))
            fp = [(x * dims[0] / 2, z * dims[2] / 2) for x, z in pts]
        objects.append(
            Object(
                id=f"o{k}",
                dimensions=dims,
                position=tuple(rng.uniform(-0.2, 0.2, 3)),
                rotation=_quat_y(rng.uniform(-180, 180)),
                footprint=fp,
            )
        )
    return precompute(_scene(objects))


class TestPrismProperties(unittest.TestCase):
    """200 random yaw-only pairs: the prism test is symmetric in (a, b), and a
    prism never collides where its box envelope does not."""

    def test_symmetry_and_envelope_invariant(self):
        geom = _random_prism_scene(20260912, 200)
        n_colliding = 0
        for p in range(200):
            i, j = 2 * p, 2 * p + 1
            ab, ba = check_prism_pair(geom, i, j), check_prism_pair(geom, j, i)
            with self.subTest(pair=p):
                self.assertEqual(ab.narrow_phase, "sat_prism")
                self.assertEqual(ab.colliding, ba.colliding)
                self.assertAlmostEqual(
                    ab.penetration_depth_m, ba.penetration_depth_m, delta=1e-12
                )
                if ab.colliding:
                    n_colliding += 1
                    np.testing.assert_allclose(ab.axis, -ba.axis, atol=1e-12)
                    # The envelope can only over-report, never under-report.
                    self.assertTrue(check_collision(geom.obbs[i], geom.obbs[j]).colliding)
        self.assertGreater(n_colliding, 20)  # both verdicts actually exercised

    def test_collide_scene_is_a_subset_of_the_envelope_answer(self):
        geom = _random_prism_scene(7, 10)
        prism_pairs = {tuple(sorted((r.a_id, r.b_id))) for r in collide_scene(geom)}
        envelope_pairs = set()
        for i in range(geom.n):
            for j in range(i + 1, geom.n):
                if check_collision(geom.obbs[i], geom.obbs[j]).colliding:
                    envelope_pairs.add(tuple(sorted((geom.ids[i], geom.ids[j]))))
        self.assertTrue(prism_pairs <= envelope_pairs)
        self.assertLess(len(prism_pairs), len(envelope_pairs))  # the scan really prunes


class TestPrismPerf(unittest.TestCase):
    """Same dense 20-object scene as tests/test_collision.py's TestPerf, but half
    the objects are octagonal prisms, so 78 of the 100 surviving pairs leave the
    batched path for the per-pair prism loop. Sanity bound, not a benchmark:
    fastest of 5 rounds (a mean is hostage to whatever else the box is doing)."""

    def test_dense_mixed_scene(self):
        rng = np.random.default_rng(20260911)
        fp = _ngon(8, 0.1)
        objects = []
        for k in range(20):
            pos = tuple(np.array([k % 3, (k // 3) % 3, k // 9]) * 0.19
                        + rng.uniform(-0.005, 0.005, 3))
            objects.append(
                Object(id=f"o{k}", dimensions=(0.2, 0.2, 0.2), position=pos,
                       footprint=fp if k % 2 else None)
            )
        geom = precompute(_scene(objects))
        self.assertGreater(len(collide_scene(geom)), 30)
        best = min(_ms_per_call(geom, 10) for _ in range(5))
        self.assertLess(best, 25.0, f"collide_scene took {best:.3f} ms/call")


def _ms_per_call(geom, calls):
    t0 = time.perf_counter()
    for _ in range(calls):
        collide_scene(geom)
    return (time.perf_counter() - t0) / calls * 1e3


if __name__ == "__main__":
    unittest.main()
