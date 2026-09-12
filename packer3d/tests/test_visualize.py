"""Both example results render to PNG, quickly (matplotlib is a dev-only dependency)."""
import json
import time
from pathlib import Path

import numpy as np
import pytest

from packer3d.visualize import _box, _cylinder  # noqa: E402 -- pure geometry, no matplotlib needed

pytest.importorskip("matplotlib")
from packer3d.visualize import main, render  # noqa: E402

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.mark.parametrize("name", ["suitcase", "dragon"])
def test_example_renders_fast(tmp_path, name):
    src = EXAMPLES / f"{name}_result.json"
    assert main([str(src), str(tmp_path / "naive.png"), "naive"]) == 0  # CLI path; also warms lazy imports
    out = tmp_path / "optimized.png"
    t = time.perf_counter()
    render(json.loads(src.read_text())["optimized"], str(out), "optimized")
    assert time.perf_counter() - t < 2.0
    assert out.stat().st_size > 10_000


def test_box_geometry_is_pinned():
    """A future visual tweak must not silently move, resize, or reorient a box."""
    faces = _box((1.0, 2.0, 3.0), (4.0, 5.0, 6.0))
    assert len(faces) == 6
    bottom, top, front, back, right, left = (np.asarray(f) for f in faces)
    np.testing.assert_allclose(bottom, [[1, 2, 3], [1, 7, 3], [5, 7, 3], [5, 2, 3]])
    np.testing.assert_allclose(top, [[1, 2, 9], [5, 2, 9], [5, 7, 9], [1, 7, 9]])
    np.testing.assert_allclose(front, [[1, 2, 3], [5, 2, 3], [5, 2, 9], [1, 2, 9]])
    np.testing.assert_allclose(back, [[5, 7, 3], [1, 7, 3], [1, 7, 9], [5, 7, 9]])
    np.testing.assert_allclose(right, [[5, 2, 3], [5, 7, 3], [5, 7, 9], [5, 2, 9]])
    np.testing.assert_allclose(left, [[1, 2, 3], [1, 2, 9], [1, 7, 9], [1, 7, 3]])
    # every face is a planar quad, and the 6 faces bound exactly the requested box
    all_pts = np.concatenate(faces)
    np.testing.assert_allclose(all_pts.min(0), [1, 2, 3])
    np.testing.assert_allclose(all_pts.max(0), [5, 7, 9])


@pytest.mark.parametrize("n", [8, 24, 40])
@pytest.mark.parametrize("axis", ["x", "y", "z"])
def test_cylinder_geometry_is_pinned(axis, n):
    """Segment count (n) is a pure rendering-smoothness knob: it must never change the
    cylinder's position, radius, height, or axis -- only how round it looks."""
    center, r, h = np.array([1.0, 2.0, 3.0]), 2.0, 10.0
    faces = _cylinder(center, axis, r, h, n=n)
    assert len(faces) == n + 2
    sides, hi_cap, lo_cap = faces[:-2], faces[-2], faces[-1]
    assert len(sides) == n and len(hi_cap) == n and len(lo_cap) == n

    a = np.eye(3)["xyz".index(axis)]
    all_pts = np.concatenate(faces)
    off_axis = all_pts - center - np.outer(all_pts @ a - center @ a, a)  # component perpendicular to axis
    np.testing.assert_allclose(np.linalg.norm(off_axis, axis=1), r, atol=1e-9)  # every vertex sits at radius r
    along = (all_pts - center) @ a
    np.testing.assert_allclose(sorted({round(v, 9) for v in along}), [-h / 2, h / 2])  # only the two end caps
