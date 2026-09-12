"""`scripts/ar_sim.py::solids_overlap` must tell cavity nesting from a real collision.

Bounding boxes cannot. Since the solver learned to seat an item in another item's scanned
cavity, two bounding boxes legally interpenetrate -- an l-shape's missing quadrant with
something sitting in it is the normal case. A bbox-only check reported a 70 cm3 "overlap" on a
correct plan and made the AR gate look flaky (5 failures in 6 runs). These pin both directions,
so the check can neither go blind nor go back to crying wolf.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "packer3d"))
from packer3d.models import Item  # noqa: E402

_spec = importlib.util.spec_from_file_location("ar_sim", _ROOT / "scripts" / "ar_sim.py")
ar_sim = importlib.util.module_from_spec(_spec)
sys.modules["ar_sim"] = ar_sim  # dataclasses resolves annotations through sys.modules
_spec.loader.exec_module(ar_sim)


def _placed(item_id, x, size=(0.10, 0.10, 0.10)):
    """An app-frame placement: position/size are x,y,z dicts and y is up."""
    return {"itemId": item_id, "label": item_id, "step": 1, "rotation": "XYZ",
            "position": {"x": x, "y": 0.0, "z": 0.0},
            "size": {"x": size[0], "y": size[1], "z": size[2]}}


def _cube_doc(item_id):
    return {"id": item_id, "dimensions": [0.10, 0.10, 0.10], "cellSize": 0.05,
            "heights": [[0.10, 0.10], [0.10, 0.10]]}


class SolidsOverlapTest(unittest.TestCase):
    def _items(self, *docs):
        return {d["id"]: Item.from_scanned_heightmap(d) for d in docs}

    def test_separated_boxes_do_not_overlap(self):
        items = self._items(_cube_doc("a"), _cube_doc("b"))
        self.assertFalse(ar_sim.solids_overlap(_placed("a", 0.0), _placed("b", 0.20), items))

    def test_real_collision_is_caught(self):
        """Without this the test above proves nothing -- a check that always says False passes it."""
        items = self._items(_cube_doc("a"), _cube_doc("b"))
        self.assertTrue(ar_sim.solids_overlap(_placed("a", 0.0), _placed("b", 0.05), items))

    def test_item_seated_in_a_cavity_is_not_a_collision(self):
        """An L-shaped host -- a full-height column beside an empty notch -- with a block in
        the notch. The bounding boxes overlap; the solids do not."""
        # rows index x: a full-height cell at x 0.00-0.10 and an empty one at 0.10-0.20
        host = {"id": "host", "dimensions": [0.20, 0.10, 0.10], "cellSize": 0.10,
                "heights": [[0.10], [0.00]]}
        guest = _cube_doc("guest")
        items = self._items(host, guest)
        a = _placed("host", 0.0, size=(0.20, 0.10, 0.10))
        b = _placed("guest", 0.10)
        self.assertTrue(
            ar_sim.boxes_overlap([0.0, 0.0, 0.0], [0.20, 0.10, 0.10], [0.10, 0.0, 0.0], [0.10, 0.10, 0.10]),
            "fixture is wrong: the bounding boxes must overlap for this test to mean anything")
        self.assertFalse(ar_sim.solids_overlap(a, b, items),
                         "a block sitting in the host's scanned notch is nesting, not a collision")


if __name__ == "__main__":
    unittest.main()
