import unittest

from physics.compressibility import (
    combined_collision_allowance_m,
    compression_allowance_m,
    container_wall_allowance_m,
)
from physics.geometry import obb_from
from physics.schema import Object


def obj(id_, dims=(0.2, 0.2, 0.2), rigidity="rigid", k=1.0):
    return Object(
        id=id_,
        dimensions=dims,
        position=(0.0, 0.0, 0.0),
        rigidity=rigidity,
        compressibility_k=k,
    )


class TestCompressionAllowanceM(unittest.TestCase):
    def test_rigid_always_zero_regardless_of_k(self):
        for k in (1.0, 2.0, 100.0):
            o = obj("r", rigidity="rigid", k=k)
            self.assertEqual(compression_allowance_m(o, 1.0), 0.0)

    def test_soft_k1_is_zero_nothing_to_compress(self):
        o = obj("s", rigidity="soft", k=1.0)
        self.assertAlmostEqual(compression_allowance_m(o, 1.0), 0.0, places=9)

    def test_soft_k2_positive_and_below_cap(self):
        o = obj("s", rigidity="soft", k=2.0)
        extent = 1.0
        allowance = compression_allowance_m(o, extent)
        self.assertGreater(allowance, 0.0)
        self.assertLess(allowance, 0.95 * extent)
        # extent * (1 - 1/2) * 1.0 = 0.5 * extent
        self.assertAlmostEqual(allowance, 0.5 * extent, places=9)

    def test_semi_is_half_of_soft_for_same_k(self):
        extent = 1.0
        soft = obj("soft", rigidity="soft", k=2.0)
        semi = obj("semi", rigidity="semi", k=2.0)
        self.assertAlmostEqual(
            compression_allowance_m(semi, extent),
            0.5 * compression_allowance_m(soft, extent),
            places=9,
        )

    def test_extreme_k_clamped_to_95_percent_ceiling(self):
        extent = 1.0
        o = obj("s", rigidity="soft", k=1e9)
        allowance = compression_allowance_m(o, extent)
        self.assertLess(allowance, extent)
        self.assertAlmostEqual(allowance, 0.95 * extent, places=6)


class TestCombinedCollisionAllowanceM(unittest.TestCase):
    def test_two_soft_objects_sum_allowances(self):
        import numpy as np

        a = obj("a", dims=(1.0, 1.0, 1.0), rigidity="soft", k=2.0)
        b = obj("b", dims=(1.0, 1.0, 1.0), rigidity="soft", k=2.0)
        obb_a, obb_b = obb_from(a), obb_from(b)
        axis = np.array([1.0, 0.0, 0.0])
        total = combined_collision_allowance_m(a, b, axis, obb_a, obb_b)
        each = compression_allowance_m(a, 1.0)
        self.assertAlmostEqual(total, 2 * each, places=9)

    def test_rigid_plus_soft_only_soft_contributes(self):
        import numpy as np

        a = obj("a", dims=(1.0, 1.0, 1.0), rigidity="rigid", k=5.0)
        b = obj("b", dims=(1.0, 1.0, 1.0), rigidity="soft", k=2.0)
        obb_a, obb_b = obb_from(a), obb_from(b)
        axis = np.array([1.0, 0.0, 0.0])
        total = combined_collision_allowance_m(a, b, axis, obb_a, obb_b)
        self.assertAlmostEqual(total, compression_allowance_m(b, 1.0), places=9)


class TestContainerWallAllowanceM(unittest.TestCase):
    def test_matches_compression_allowance_m(self):
        o = obj("s", rigidity="soft", k=3.0)
        self.assertAlmostEqual(
            container_wall_allowance_m(o, 0.4), compression_allowance_m(o, 0.4), places=9
        )


if __name__ == "__main__":
    unittest.main()
