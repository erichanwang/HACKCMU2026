"""Tests for physics.collision (OBB-OBB SAT).

Run: python3 -m unittest tests.test_collision -v
"""
from __future__ import annotations

import math
import time
import unittest

import numpy as np

from physics.collision import aabb_overlap, check_collision, check_pairs, collide_scene
from physics.geometry import OBB, obb_from
from physics.scene_geometry import precompute
from physics.schema import Container, Object, Scene
from tests import fixtures


def box(center, half_extents, axes=None, id_="b"):
    return OBB(
        center=np.asarray(center, dtype=float),
        axes=np.eye(3) if axes is None else axes,
        half_extents=np.asarray(half_extents, dtype=float),
        id=id_,
    )


def rotz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def rotx(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


class TestNoCollision(unittest.TestCase):
    def test_far_apart(self):
        a = box((0, 0, 0), (1, 1, 1), id_="a")
        b = box((100, 100, 100), (1, 1, 1), id_="b")
        res = check_collision(a, b)
        self.assertFalse(res.colliding)
        self.assertEqual(res.penetration_depth_m, 0.0)
        self.assertFalse(aabb_overlap(a, b))


class TestFaceFace(unittest.TestCase):
    def test_axis_aligned_overlap(self):
        # Both 2x2x2 cubes (half-extent 1), B offset 1.5 along X: overlap = 1+1-1.5 = 0.5
        a = box((0, 0, 0), (1, 1, 1), id_="a")
        b = box((1.5, 0, 0), (1, 1, 1), id_="b")
        res = check_collision(a, b)
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 0.5, delta=1e-6)
        np.testing.assert_allclose(np.abs(res.axis), [1, 0, 0], atol=1e-9)


class TestRotated45(unittest.TestCase):
    def test_rotated_box_collides(self):
        # A axis-aligned cube at origin; B same size, rotated 45deg about Z,
        # positioned close enough along X that its rotated corner pokes into A.
        a_obj = Object(id="a", dimensions=(2, 2, 2), position=(0, 0, 0))
        # 45 deg about Z: q = (0,0,sin(22.5deg),cos(22.5deg))
        half = math.radians(45) / 2
        b_obj = Object(
            id="b",
            dimensions=(2, 2, 2),
            position=(1.8, 0, 0),
            rotation=(0, 0, math.sin(half), math.cos(half)),
        )
        a = obb_from(a_obj)
        b = obb_from(b_obj)
        res = check_collision(a, b)
        self.assertTrue(res.colliding)
        self.assertGreater(res.penetration_depth_m, 0.0)

    def test_rotated_box_no_collision_when_far(self):
        a_obj = Object(id="a", dimensions=(2, 2, 2), position=(0, 0, 0))
        half = math.radians(45) / 2
        b_obj = Object(
            id="b",
            dimensions=(2, 2, 2),
            position=(5, 0, 0),
            rotation=(0, 0, math.sin(half), math.cos(half)),
        )
        res = check_collision(obb_from(a_obj), obb_from(b_obj))
        self.assertFalse(res.colliding)


class TestContainment(unittest.TestCase):
    def test_small_box_inside_big_box(self):
        big = box((0, 0, 0), (5, 5, 5), id_="big")
        small = box((0.5, 0, 0), (1, 1, 1), id_="small")
        res = check_collision(big, small)
        self.assertTrue(res.colliding)
        # overlap along X = 5+1-0.5=5.5 ; Y,Z = 5+1-0=6 ; min is X
        self.assertAlmostEqual(res.penetration_depth_m, 5.5, delta=1e-6)
        np.testing.assert_allclose(np.abs(res.axis), [1, 0, 0], atol=1e-9)


class TestEdgeEdge(unittest.TestCase):
    """Two rods, rotated relative to each other about a shared-free axis, so
    that only one of the 9 cross-product axes separates them -- all 6 face
    axes still show overlap. Constructed/verified numerically (see collision.py
    module docstring for the general SAT axis set); not hand-derivable in
    closed form for an arbitrary offset, so we assert booleans + that the
    reported axis is not parallel to any face axis, rather than an exact depth.
    """

    def setUp(self):
        self.Ra = np.eye(3)
        self.Rb = rotz(45) @ rotx(45)
        self.ha = np.array([1.0, 0.05, 0.05])
        self.hb = np.array([0.05, 0.05, 1.0])
        u = np.cross(self.Ra[:, 0], self.Rb[:, 1])
        self.u = u / np.linalg.norm(u)

    def _pair(self, t):
        a = OBB(center=np.zeros(3), axes=self.Ra, half_extents=self.ha, id="a")
        b = OBB(center=t * self.u, axes=self.Rb, half_extents=self.hb, id="b")
        return a, b

    def test_colliding_via_cross_axis(self):
        a, b = self._pair(0.1)
        res = check_collision(a, b)
        self.assertTrue(res.colliding)
        # The MTV axis must not be (anti)parallel to any of the 6 face axes.
        for face_axis in list(self.Ra.T) + list(self.Rb.T):
            self.assertLess(abs(float(res.axis @ face_axis)), 0.9)

    def test_not_colliding_but_all_face_axes_still_overlap(self):
        a, b = self._pair(0.5)
        res = check_collision(a, b)
        self.assertFalse(res.colliding)
        d = b.center - a.center
        for face_axis in list(self.Ra.T) + list(self.Rb.T):
            ra = np.sum(np.abs(face_axis @ self.Ra) * self.ha)
            rb = np.sum(np.abs(face_axis @ self.Rb) * self.hb)
            overlap = ra + rb - abs(float(face_axis @ d))
            self.assertGreater(overlap, 0.0)  # face axes alone would say "colliding"


class TestCornerCorner(unittest.TestCase):
    def test_exact_corner_touch_not_colliding(self):
        a = box((0, 0, 0), (1, 1, 1), id_="a")
        b = box((2, 2, 2), (1, 1, 1), id_="b")
        res = check_collision(a, b)
        self.assertFalse(res.colliding)

    def test_near_corner_touch_colliding(self):
        a = box((0, 0, 0), (1, 1, 1), id_="a")
        b = box((1.999, 1.999, 1.999), (1, 1, 1), id_="b")
        res = check_collision(a, b)
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 0.001, delta=1e-6)


class TestTouchingAndEpsilon(unittest.TestCase):
    def test_exactly_touching_not_colliding(self):
        a = box((0, 0, 0), (1, 1, 1), id_="a")
        b = box((2, 0, 0), (1, 1, 1), id_="b")  # faces share the plane x=1 exactly
        res = check_collision(a, b)
        self.assertFalse(res.colliding)
        self.assertEqual(res.penetration_depth_m, 0.0)

    def test_tiny_overlap_beyond_epsilon_is_colliding(self):
        a = box((0, 0, 0), (1, 1, 1), id_="a")
        b = box((2 - 1e-4, 0, 0), (1, 1, 1), id_="b")
        res = check_collision(a, b, epsilon=1e-6)
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 1e-4, delta=1e-6)

    def test_overlap_within_epsilon_is_not_colliding(self):
        a = box((0, 0, 0), (1, 1, 1), id_="a")
        b = box((2 - 1e-7, 0, 0), (1, 1, 1), id_="b")
        res = check_collision(a, b, epsilon=1e-6)
        self.assertFalse(res.colliding)


def _scene(objects, container_dims=(4.0, 4.0, 4.0)):
    return Scene(
        container=Container(id="c", dimensions=container_dims, position=(0.0, 0.0, 0.0)),
        objects=objects,
    )


def _random_pair_geom(seed, n_pairs):
    """A scene of 2*n_pairs random OBBs; pair p is objects (2p, 2p+1)."""
    rng = np.random.default_rng(seed)
    objects = []
    for k in range(2 * n_pairs):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)  # uniform-ish random unit quaternion (x, y, z, w)
        objects.append(
            Object(
                id=f"o{k}",
                dimensions=tuple(rng.uniform(0.02, 0.5, 3)),
                position=tuple(rng.uniform(-0.6, 0.6, 3)),
                rotation=tuple(q),
            )
        )
    return precompute(_scene(objects)), np.arange(2 * n_pairs).reshape(-1, 2)


def _brute_force_colliding(a: OBB, b: OBB, epsilon: float = 1e-6) -> bool:
    """Independent SAT reference: the OLD approach -- build all 15 axes
    explicitly with np.cross and normalize them, skipping degenerate ones.
    Deliberately NOT the Ericson formulation under test."""
    axes = [a.axes[:, i] for i in range(3)] + [b.axes[:, j] for j in range(3)]
    for i in range(3):
        for j in range(3):
            c = np.cross(a.axes[:, i], b.axes[:, j])
            n = float(np.linalg.norm(c))
            if n >= 1e-8:
                axes.append(c / n)
    d = b.center - a.center
    for axis in axes:
        ra = float(np.sum(np.abs(axis @ a.axes) * a.half_extents))
        rb = float(np.sum(np.abs(axis @ b.axes) * b.half_extents))
        if ra + rb - abs(float(axis @ d)) <= epsilon:
            return False
    return True


class TestBatchedEquivalence(unittest.TestCase):
    """check_pairs (m pairs at once) == check_collision (m=1) == brute-force SAT."""

    def test_200_random_pairs(self):
        geom, pairs = _random_pair_geom(20260911, 200)
        batched = check_pairs(geom, pairs)
        self.assertEqual(len(batched), len(pairs))
        n_colliding = 0
        for row, (i, j) in enumerate(pairs):
            a, b = geom.obbs[i], geom.obbs[j]
            got, single = batched[row], check_collision(a, b)
            with self.subTest(pair=row):
                self.assertEqual(got.a_id, a.id)
                self.assertEqual(got.b_id, b.id)
                self.assertEqual(got.colliding, single.colliding)
                self.assertAlmostEqual(
                    got.penetration_depth_m, single.penetration_depth_m, delta=1e-9
                )
                if got.colliding:
                    n_colliding += 1
                    np.testing.assert_allclose(got.axis, single.axis, atol=1e-12)
                    np.testing.assert_allclose(got.contact_point, single.contact_point, atol=1e-12)
                # Cross-check against the independent (old-style) SAT, except
                # where an edge pair is near-parallel: there the padded
                # formulation is deliberately conservative and the two
                # legitimately disagree.
                if np.abs(a.axes.T @ b.axes).max() <= 1 - 1e-6:
                    self.assertEqual(got.colliding, _brute_force_colliding(a, b))
        self.assertGreater(n_colliding, 5)  # both branches actually exercised

    def test_empty_pairs(self):
        geom, _ = _random_pair_geom(1, 2)
        self.assertEqual(check_pairs(geom, np.zeros((0, 2), dtype=int)), [])


class TestParallelEdges(unittest.TestCase):
    """The classic degenerate case: every edge pair is parallel, so all 9
    cross-product axes are null. The 6 face axes must still decide, and the
    EPS_PARALLEL padding must not turn a real gap into a collision."""

    def test_gap_along_x_not_colliding(self):
        a = box((0, 0, 0), (0.1, 0.1, 0.1), id_="a")
        b = box((0.25, 0, 0), (0.1, 0.1, 0.1), id_="b")  # 0.05 m gap along +x
        res = check_collision(a, b)
        self.assertFalse(res.colliding)
        self.assertEqual(res.penetration_depth_m, 0.0)

    def test_known_overlap_is_exact(self):
        a = box((0, 0, 0), (0.1, 0.1, 0.1), id_="a")
        b = box((0.19, 0, 0), (0.1, 0.1, 0.1), id_="b")  # overlap = 0.2 - 0.19
        res = check_collision(a, b)
        self.assertTrue(res.colliding)
        self.assertAlmostEqual(res.penetration_depth_m, 0.01, delta=1e-9)
        np.testing.assert_allclose(res.axis, [1, 0, 0], atol=1e-12)


class TestEdgeEdge30_60(unittest.TestCase):
    """Second cross-axis-only case (30 deg about Z, 60 deg about X): two rods
    offset along the common perpendicular of their long axes. Only a
    cross-product axis separates them -- all 6 face axes still overlap."""

    def setUp(self):
        self.Ra = np.eye(3)
        self.Rb = rotz(30) @ rotx(60)
        self.ha = np.array([1.0, 0.05, 0.05])  # long along local x
        self.hb = np.array([0.05, 0.05, 1.0])  # long along local z
        u = np.cross(self.Ra[:, 0], self.Rb[:, 2])
        self.u = u / np.linalg.norm(u)

    def _pair(self, t):
        return (
            OBB(center=np.zeros(3), axes=self.Ra, half_extents=self.ha, id="a"),
            OBB(center=t * self.u, axes=self.Rb, half_extents=self.hb, id="b"),
        )

    def test_colliding_when_close(self):
        a, b = self._pair(0.05)
        self.assertTrue(check_collision(a, b).colliding)

    def test_separated_only_by_cross_axis(self):
        a, b = self._pair(0.2)
        self.assertFalse(check_collision(a, b).colliding)
        d = b.center - a.center
        for face_axis in list(self.Ra.T) + list(self.Rb.T):
            ra = np.sum(np.abs(face_axis @ self.Ra) * self.ha)
            rb = np.sum(np.abs(face_axis @ self.Rb) * self.hb)
            overlap = ra + rb - abs(float(face_axis @ d))
            self.assertGreater(overlap, 0.0)  # face axes alone would say "colliding"


class TestCollideScene(unittest.TestCase):
    def test_finds_the_one_engineered_overlap(self):
        results = collide_scene(precompute(fixtures.scene_with_collision()))
        self.assertEqual(
            [tuple(sorted((r.a_id, r.b_id))) for r in results], [("laptop", "shoe")]
        )
        # fixture docstring: shoe shifted so it overlaps laptop by exactly 0.02 m
        self.assertAlmostEqual(results[0].penetration_depth_m, 0.02, delta=1e-9)

    def test_valid_scene_has_no_collisions(self):
        self.assertEqual(collide_scene(precompute(fixtures.valid_packed_scene())), [])


class TestPerf(unittest.TestCase):
    """Sanity bound, not a benchmark: a dense 20-object scene (0.20 m boxes on a
    0.19 m grid + jitter -> ~100 of the 190 pairs overlap) must stay well under
    a millisecond-scale budget per `collide_scene` call."""

    def test_dense_scene_under_5ms(self):
        rng = np.random.default_rng(20260911)
        objects = [
            Object(
                id=f"o{k}",
                dimensions=(0.2, 0.2, 0.2),
                position=tuple(
                    np.array([k % 3, (k // 3) % 3, k // 9]) * 0.19 + rng.uniform(-0.005, 0.005, 3)
                ),
            )
            for k in range(20)
        ]
        geom = precompute(_scene(objects))
        self.assertGreater(len(collide_scene(geom)), 50)  # genuinely dense
        calls = 20
        t0 = time.perf_counter()
        for _ in range(calls):
            collide_scene(geom)
        per_call_ms = (time.perf_counter() - t0) / calls * 1e3
        self.assertLess(per_call_ms, 5.0, f"collide_scene took {per_call_ms:.3f} ms/call")


if __name__ == "__main__":
    unittest.main()
