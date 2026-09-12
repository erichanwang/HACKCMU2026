"""`server/app_plan.py::to_app_plan`: packer3d result -> the iOS app's plan JSON.

Pins the mapping described in `app_plan.py`'s module docstring: the frame is a y/z swap
(`app(x, y, z) = (packer_x, packer_z, packer_y)`), and `_ROTATION` re-reads a packer3d
orientation string into the app's `AxisRotation` (`packing-core/Sources/PackingPlan/
AxisRotation.swift`). `to_app_plan` is a pure dict transform -- no Mongo, no HTTP.
"""
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT / "server", _ROOT / "packer3d"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from app_plan import _ROTATION, to_app_plan  # noqa: E402
from packer3d.models import Item  # noqa: E402


def suitcase(width, height, depth):
    return {"_id": "s1", "name": "test suitcase", "dimensions": [width, height, depth]}


def placement(item_id="i1", position=(0.0, 0.0, 0.0), dims=(1.0, 1.0, 1.0), orientation="xyz"):
    return {"item_id": item_id, "position": list(position), "dims": list(dims), "orientation": orientation}


class TestFrame(unittest.TestCase):
    """app(x, y, z) = (packer_x, packer_z, packer_y) for both position and size."""

    def test_position_and_size_swap_y_and_z(self):
        result = {"placements": [placement(position=(1.0, 2.0, 3.0), dims=(4.0, 5.0, 6.0))]}
        p = to_app_plan(result, suitcase(10, 20, 30), {})["placements"][0]
        self.assertEqual(p["position"], {"x": 1.0, "y": 3.0, "z": 2.0})
        self.assertEqual(p["size"], {"x": 4.0, "y": 6.0, "z": 5.0})

    def test_container_dimensions_are_width_height_depth_verbatim(self):
        plan = to_app_plan({"placements": []}, suitcase(0.4, 0.2, 0.3), {})
        self.assertEqual(plan["container"]["dimensions"], {"x": 0.4, "y": 0.2, "z": 0.3})

    def test_single_zone_is_the_whole_interior(self):
        plan = to_app_plan({"placements": []}, suitcase(0.4, 0.2, 0.3), {})
        self.assertEqual(plan["container"]["zones"], [{
            "id": "interior", "label": "Interior",
            "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
            "size": {"x": 0.4, "y": 0.2, "z": 0.3},
        }])


class TestRotationTable(unittest.TestCase):
    """Every packer3d box-orientation string maps to `_ROTATION`'s value, and that value is
    consistent: `size[axis] == the item's own dimension named by the rotation letter at
    `axis`` -- the same assertion `server/check.py` runs end to end ("size must be the
    item's own [w, h, d] permuted by rotation")."""

    def test_all_six_box_orientations(self):
        w, h, d = 0.30, 0.20, 0.10  # distinct, so a wrong permutation shows up as a wrong number
        item = Item.box("i1", w, d, h)  # packer dims = (width, depth, height): the scanned-item form
        local = {"X": w, "Y": h, "Z": d}
        seen = set()
        for o in item.orientations():
            seen.add(o.name)
            result = {"placements": [placement(dims=o.dims, orientation=o.name)]}
            out = to_app_plan(result, suitcase(1, 1, 1), {})["placements"][0]
            self.assertEqual(out["rotation"], _ROTATION[o.name])
            for axis, letter in zip("xyz", out["rotation"]):
                self.assertAlmostEqual(out["size"][axis], local[letter],
                                        msg=(o.name, out["rotation"], out["size"], local))
        self.assertEqual(seen, {k for k in _ROTATION if not k.startswith("cyl_")})  # distinct dims => all six box orientations are legal


class TestCylinderRotationFallback(unittest.TestCase):
    """BUG, not fixed here (app_plan.py is owned by another agent): `_ROTATION` has no entry
    for a cylinder's orientation strings, so `_ROTATION.get(orientation, "XYZ")` always
    falls back to identity. That is only correct for `cyl_axis_z` (the cylinder's own
    long/height axis already runs along bag Z == up, same as an upright box needing no
    rotation). For `cyl_axis_x` / `cyl_axis_y` the cylinder is lying on its side -- its long
    axis runs along bag X or bag Y -- so "XYZ" mislabels which local axis is the long one.
    `size` (the bounding box) stays correct; only `rotation`, which a renderer uses via
    `AxisRotation.localAxis(forBagAxis:)` to orient the mesh/label, is wrong -- it would draw
    the item standing upright instead of lying on its side. Left failing on purpose; see the
    task report for the exact input/output pair.
    """

    def test_cylinder_lying_on_its_side_gets_a_wrong_rotation(self):
        diameter, cyl_height = 0.08, 0.30  # distinct, so a wrong permutation shows a wrong number
        item = Item.cylinder("bottle", radius=diameter / 2, height=cyl_height,
                              keep_upright=False, allow_lay_down=True)
        local = {"X": diameter, "Y": cyl_height, "Z": diameter}
        for o in item.orientations():
            if o.name == "cyl_axis_z":
                continue  # upright: identity really is correct here
            with self.subTest(orientation=o.name):
                result = {"placements": [placement(item_id="bottle", dims=o.dims, orientation=o.name)]}
                out = to_app_plan(result, suitcase(1, 1, 1), {})["placements"][0]
                for axis, letter in zip("xyz", out["rotation"]):
                    self.assertAlmostEqual(out["size"][axis], local[letter],
                                            msg=(o.name, out["rotation"], out["size"], local))


class TestPlanMetadata(unittest.TestCase):
    def test_steps_are_1_based_in_placement_order(self):
        result = {"placements": [placement("a"), placement("b"), placement("c")]}
        plan = to_app_plan(result, suitcase(1, 1, 1), {})
        self.assertEqual([p["step"] for p in plan["placements"]], [1, 2, 3])

    def test_label_falls_back_to_item_id_and_note_to_empty(self):
        plan = to_app_plan({"placements": [placement("a")]}, suitcase(1, 1, 1), {})
        p = plan["placements"][0]
        self.assertEqual(p["label"], "a")
        self.assertEqual(p["note"], "")

    def test_label_and_note_come_from_the_item_document_when_present(self):
        items_by_id = {"a": {"label": "Running shoes", "description": "soles down"}}
        plan = to_app_plan({"placements": [placement("a")]}, suitcase(1, 1, 1), items_by_id)
        p = plan["placements"][0]
        self.assertEqual(p["label"], "Running shoes")
        self.assertEqual(p["note"], "soles down")

    def test_unpacked_is_not_part_of_the_plan(self):
        result = {"placements": [], "unpacked": [{"id": "x", "reason": "no room"}]}
        plan = to_app_plan(result, suitcase(1, 1, 1), {})
        self.assertNotIn("unpacked", plan)


if __name__ == "__main__":
    unittest.main()
