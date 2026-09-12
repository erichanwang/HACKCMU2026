import unittest

from physics.prepack import packable, physics_object


def doc(**over):
    return {"id": "x", "dimensions": [0.3, 0.1, 0.2], "rigidity": "rigid", "compressibility": 1.0,
            "mass": 0.5, "keepUpright": False} | over


class TestPrepack(unittest.TestCase):
    def test_soft_item_packs_at_height_over_k(self):
        out = packable(doc(rigidity="soft", compressibility=2.0))
        self.assertAlmostEqual(out["compressibility"], 2.0)
        self.assertNotIn("fragile", out)
        self.assertNotIn("keep_upright", out)

    def test_rigid_and_fragile_never_compress(self):
        self.assertEqual(packable(doc(rigidity="rigid", compressibility=3.0))["compressibility"], 1.0)
        out = packable(doc(rigidity="fragile", compressibility=3.0))
        self.assertEqual(out["compressibility"], 1.0)
        self.assertTrue(out["fragile"])

    def test_compression_is_capped_at_95_percent(self):
        self.assertLessEqual(packable(doc(rigidity="soft", compressibility=1000.0))["compressibility"], 20.0 + 1e-9)

    def test_keep_upright_and_mass_reach_the_solver(self):
        out = packable(doc(keepUpright=True, mass=1.5))
        self.assertTrue(out["keep_upright"])
        self.assertEqual(out["mass"], 1.5)
        self.assertEqual(physics_object(doc(keepUpright=True)).constraints.orientation_lock, "this_side_up")

    def test_document_passes_through(self):
        d = doc(heights=[[0.1]], cellSize=0.01, suitcaseId="s")
        out = packable(d)
        for key in ("heights", "cellSize", "suitcaseId", "dimensions", "rigidity"):
            self.assertEqual(out[key], d[key])

    def test_footprint_reaches_the_object_and_the_solver_document_unchanged(self):
        fp = [[-0.15, -0.1], [0.15, -0.1], [0.15, 0.0], [0.0, 0.0], [0.0, 0.1], [-0.15, 0.1]]
        d = doc(footprint=fp)
        obj = physics_object(d)
        self.assertEqual(obj.footprint, [tuple(p) for p in fp])
        self.assertEqual(packable(d)["footprint"], fp)

    def test_missing_footprint_stays_none(self):
        self.assertIsNone(physics_object(doc()).footprint)
        self.assertNotIn("footprint", packable(doc()))


if __name__ == "__main__":
    unittest.main()
