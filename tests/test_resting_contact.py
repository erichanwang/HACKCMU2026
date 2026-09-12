"""`resting_pairs` must report contact, not proximity.

Two false positives were reaching `FRAGILE_OBJECT_OVERLOADED` and failing real
packing plans (`tools/packbench`: camera_kit_fragile and carryon_overfilled):

* two boxes packed flush edge to edge overlap in XZ by ~1e-17 m^2 out of the OBB
  vertex math, and `area > 0.0` called that a contact;
* `constraints.check_constraints` passed its 2 cm *adjacency* tolerance to the
  resting graph, so an item with 2 cm of clear air above a fragile one was
  reported as resting on it.

Both directions matter, so each case here has its positive control: a real
overload must still be caught.
"""
import unittest

from physics.constraints import check_constraints
from physics.schema import Constraints, Container, Object, Scene


def _scene(*objects: Object) -> Scene:
    return Scene(container=Container(id="c", dimensions=(2.0, 2.0, 2.0), position=(0.0, 1.0, 0.0)),
                 objects=list(objects))


def _fragile(id: str, position, dimensions=(0.2, 0.02, 0.2)) -> Object:
    return Object(id=id, dimensions=dimensions, position=position, mass_kg=0.1,
                  constraints=Constraints(fragile=True, cannot_support_weight=True))


def _heavy(id: str, position, dimensions=(0.2, 0.2, 0.2)) -> Object:
    return Object(id=id, dimensions=dimensions, position=position, mass_kg=5.0)


def _types(result) -> set[str]:
    violations, _warnings = result
    return {v.type for v in violations}


class RestingContactTests(unittest.TestCase):
    def test_flush_side_by_side_is_not_resting(self):
        """The camera-kit case: a heavy body beside a fragile wallet, touching
        edge to edge. Zero real footprint overlap, so nothing rests on anything."""
        fragile = _fragile("wallet", (0.0, 0.01, 0.0))
        beside = _heavy("body", (0.3, 0.1, 0.0))  # x faces touch at x = 0.1
        self.assertNotIn("FRAGILE_OBJECT_OVERLOADED", _types(check_constraints(_scene(fragile, beside))))

    def test_flush_stacked_is_still_an_overload(self):
        """Positive control for the case above: genuinely on top, still caught."""
        fragile = _fragile("wallet", (0.0, 0.01, 0.0))
        on_top = _heavy("body", (0.0, 0.12, 0.0))  # base at y = 0.02, the wallet's top
        self.assertIn("FRAGILE_OBJECT_OVERLOADED", _types(check_constraints(_scene(fragile, on_top))))

    def test_a_centimetre_of_air_is_not_resting(self):
        """The overfilled-carry-on case: real footprint overlap, but 1 cm of
        clear air. Inside the 2 cm adjacency tolerance, and not a contact."""
        fragile = _fragile("case", (0.0, 0.01, 0.0))
        above = _heavy("bundle", (0.0, 0.13, 0.0))  # base at y = 0.03, 1 cm above
        self.assertNotIn("FRAGILE_OBJECT_OVERLOADED", _types(check_constraints(_scene(fragile, above))))

    def test_a_tenth_of_a_millimetre_of_air_is_still_resting(self):
        """Positive control: the tolerance is 1e-3, so real contact with float
        noise under it still counts."""
        fragile = _fragile("case", (0.0, 0.01, 0.0))
        above = _heavy("bundle", (0.0, 0.1201, 0.0))  # base 0.1 mm above the top
        self.assertIn("FRAGILE_OBJECT_OVERLOADED", _types(check_constraints(_scene(fragile, above))))


if __name__ == "__main__":
    unittest.main()
