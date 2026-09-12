"""Material/folding preparation: axis-aware compression and refold options.

The fold geometry is checked against real folded clothes, not against the
formula that produced it -- a folded t-shirt is about 30 x 22 cm and a rolled
one about 28 cm long by 9 cm across (both looked up; see `physics.prepack`).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from physics.compressibility import (
    IN_PLANE_ANISOTROPY,
    compression_allowance_m,
    compression_allowance_on_axis_m,
    layer_axis_index,
)
from physics.prepack import fold_options, packable
from physics.schema import Object

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "packer3d"))  # packer3d/ is not on the path by default


def obj(dims=(0.30, 0.10, 0.22), rigidity="soft", k=2.0):
    return Object(
        id="s",
        dimensions=dims,
        position=(0.0, 0.0, 0.0),
        rigidity=rigidity,
        compressibility_k=k,
    )


def doc(**over):
    return {"id": "x", "dimensions": [0.30, 0.10, 0.22], "rigidity": "soft",
            "compressibility": 2.0, "mass": 0.8, "keepUpright": False} | over


class TestAxisAwareCompression(unittest.TestCase):
    def test_thinnest_axis_is_the_layer_normal(self):
        self.assertEqual(layer_axis_index((0.30, 0.10, 0.22)), 1)
        self.assertEqual(layer_axis_index((0.05, 0.30, 0.22)), 0)
        self.assertEqual(layer_axis_index((0.30, 0.22, 0.05)), 2)

    def test_layer_normal_axis_matches_the_old_scalar_exactly(self):
        # the existing callers squash the height of a scanned stack, which *is* the
        # thin axis -- the axis-aware path must not move that number.
        o = obj()
        self.assertEqual(
            compression_allowance_on_axis_m(o, 0.10, 1), compression_allowance_m(o, 0.10)
        )

    def test_in_plane_gives_far_less_than_through_the_layers(self):
        o = obj()
        through = compression_allowance_on_axis_m(o, 0.20, 1)
        in_plane = compression_allowance_on_axis_m(o, 0.20, 0)
        self.assertGreater(through, 0.0)
        self.assertGreater(in_plane, 0.0)
        self.assertLess(in_plane, through / 4.0)
        self.assertAlmostEqual(in_plane, through * IN_PLANE_ANISOTROPY, places=12)

    def test_rigid_is_zero_on_every_axis(self):
        o = obj(rigidity="rigid", k=5.0)
        for axis in range(3):
            self.assertEqual(compression_allowance_on_axis_m(o, 0.20, axis), 0.0)

    def test_old_signature_is_untouched(self):
        # validator.py and the Swift port call this one; k=2 soft still means half.
        self.assertAlmostEqual(compression_allowance_m(obj(k=2.0), 1.0), 0.5, places=9)


class TestFoldOptions(unittest.TestCase):
    def test_first_option_is_the_box_as_scanned(self):
        self.assertEqual(fold_options((0.30, 0.10, 0.22))[0], [0.30, 0.10, 0.22])

    def test_folding_in_half_conserves_volume(self):
        flat, half = fold_options((0.30, 0.10, 0.22))[:2]
        v = lambda b: b[0] * b[1] * b[2]
        self.assertAlmostEqual(v(half), v(flat), places=9)
        self.assertNotEqual(sorted(half), sorted(flat))

    def test_rolling_stays_near_the_same_volume(self):
        flat, _, rolled = fold_options((0.30, 0.10, 0.22))
        v = lambda b: b[0] * b[1] * b[2]
        self.assertLess(v(rolled), 1.5 * v(flat))  # a roll traps some air, not a lot
        self.assertGreater(v(rolled), v(flat))

    def test_one_folded_shirt_folds_to_the_real_22x15(self):
        # a single shirt: 30 x 22 cm face, ~2.5 cm of fabric.
        half = sorted(fold_options((0.30, 0.025, 0.22))[1])
        self.assertAlmostEqual(half[1], 0.15, places=3)
        self.assertAlmostEqual(half[2], 0.22, places=3)

    def test_one_rolled_shirt_matches_the_real_28x9(self):
        rolled = sorted(fold_options((0.30, 0.025, 0.22))[2])
        self.assertAlmostEqual(rolled[2], 0.28, delta=0.01)  # looked up: ~28 cm long
        self.assertAlmostEqual(rolled[0], 0.09, delta=0.01)  # looked up: ~9 cm across
        self.assertAlmostEqual(rolled[0], rolled[1], places=9)  # roughly round

    def test_nothing_folds_into_a_pencil(self):
        for dims in ((0.30, 0.10, 0.22), (0.33, 0.15, 0.40), (0.28, 0.11, 0.11), (1.2, 0.02, 0.9)):
            for box in fold_options(dims):
                self.assertGreaterEqual(min(box), min(0.06, min(dims)) - 1e-12, box)

    def test_a_thin_towel_is_not_folded_thinner_than_it_is(self):
        # already 2 cm thick: the floor drops to its own thickness rather than
        # rejecting every option, and folding still only makes it thicker.
        for box in fold_options((1.2, 0.02, 0.9)):
            self.assertGreaterEqual(min(box), 0.02 - 1e-12)

    def test_no_duplicate_options(self):
        boxes = fold_options((0.20, 0.10, 0.20))  # long == 2 * thin: flat and half coincide
        self.assertEqual(len(boxes), len({tuple(b) for b in boxes}))


class TestFoldOptionsOnScenarioItems(unittest.TestCase):
    def test_soft_items_get_options_rigid_and_fragile_do_not(self):
        self.assertGreaterEqual(len(packable(doc())["fold_options"]), 2)
        self.assertNotIn("fold_options", packable(doc(rigidity="rigid")))
        self.assertNotIn("fold_options", packable(doc(rigidity="fragile")))

    def test_the_key_is_purely_additive(self):
        d = doc(label="tshirts", cellSize=0.02)
        out = packable(d)
        # the only key packable adds to a plain soft item beyond the pre-existing
        # compressibility/mass rewrite is fold_options.
        self.assertEqual(set(out) - set(d), {"fold_options"})
        for key in ("id", "dimensions", "rigidity", "label", "cellSize"):
            self.assertEqual(out[key], d[key])

    def test_packer3d_ignores_the_unknown_key(self):
        from packer3d.scenario import load_scenario

        base = {"container": {"dims": [0.34, 0.50, 0.20]},
                "items": [{"id": "tshirts", "shape": "box", "dims": [0.30, 0.22, 0.10],
                           "rigidity": "soft", "compressibility": 2.0, "mass": 0.8}]}
        with_key = {**base, "items": [base["items"][0] | {"fold_options": [[0.22, 0.20, 0.15]]}]}
        plain = load_scenario(base)[1][0]
        folded = load_scenario(with_key)[1][0]
        self.assertEqual(folded.dims, plain.dims)
        self.assertEqual(folded.id, plain.id)


if __name__ == "__main__":
    unittest.main()
