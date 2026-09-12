"""Tests for physics.collision (OBB-OBB SAT).

Run: python3 -m unittest tests.test_collision -v
"""
from __future__ import annotations

import math
import unittest

import numpy as np

from physics.collision import aabb_overlap, check_collision
from physics.geometry import OBB, obb_from
from physics.schema import Object


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


if __name__ == "__main__":
    unittest.main()
