"""Writes one real server plan document for the Swift decoder to read (see run.sh).

`server/planner.plan()` directly -- no HTTP, no Mongo -- so the JSON the phone would
receive is produced by exactly the code the server runs. Two files are written:

  <path>            the server document, `{suitcaseId, createdAt, solver, validation,
                    plan, chosen, alternatives}`, i.e. what `GET /suitcases/{id}/plan`
                    returns and `PlanLoader.plan(fromServerDocument:)` decodes.
  <path>/../item_dims.json   `{itemId: {x, y, z}}`, each item's own dimensions in app
                    axes (X = width, Y = height, Z = depth) as the solver saw them,
                    read off the packer3d `Item`s so the compression pre-pass is not
                    re-implemented here. The Swift side checks every placement's `size`
                    against these, permuted by its `rotation`.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "server"))
import planner  # noqa: E402  (also puts the repo root and packer3d/ on sys.path)
from packer3d.scenario import load_scenario  # noqa: E402
from physics.prepack import prepare_items  # noqa: E402


def scan(item_id, w, h, d, **over):
    """A full-box scan document, as tests/test_planner.py builds them."""
    cell = 0.05
    rows, cols = math.ceil(w / cell), math.ceil(d / cell)
    return {"id": item_id, "dimensions": [w, h, d], "cellSize": cell, "heights": [[h] * cols for _ in range(rows)],
            "rigidity": "rigid", "compressibility": 1.0, "mass": 0.5, "keepUpright": False, "label": item_id} | over


def round_scan(item_id, diameter, h, **over):
    """A circular-footprint scan (a bottle seen from above), so `Item.from_scanned_heightmap`
    classifies it as a cylinder and the solver may lay it on its side (cyl_axis_x / cyl_axis_y)."""
    cell = 0.01
    n = math.ceil(diameter / cell)
    c, r = (n - 1) / 2, diameter / (2 * cell)
    heights = [[h if (i - c) ** 2 + (j - c) ** 2 <= r * r else 0.0 for j in range(n)] for i in range(n)]
    return scan(item_id, diameter, h, diameter, **over) | {"cellSize": cell, "heights": heights}


SUITCASE = {"_id": "contract-carry-on", "name": "Contract carry-on", "dimensions": [0.4, 0.2, 0.3]}
ITEMS = [
    scan("shirts", 0.3, 0.1, 0.2, rigidity="soft", compressibility=2.0, label="Folded shirts"),
    scan("bottle", 0.07, 0.18, 0.07, keepUpright=True, label="Water bottle"),
    scan("camera", 0.15, 0.08, 0.1, rigidity="fragile", label="Camera"),
    scan("book", 0.15, 0.04, 0.2, label="Paperback"),
    # taller than the bag: it can only fit lying down, which exercises the cylinder rotations
    round_scan("longbottle", 0.18, 0.35, label="Tall bottle"),
]


def main(out: Path) -> int:
    planner.TIME_BUDGET_S = 0.3
    doc = {"suitcaseId": str(SUITCASE["_id"]),
           "createdAt": datetime.now(timezone.utc).isoformat()} | planner.plan(SUITCASE, ITEMS)

    # The solver's own view of each item, for the `size` vs `rotation` check. packer3d's frame
    # is (x = length, y = width, z = up), so app (x, y, z) = (dims[0], dims[2], dims[1]).
    width, height, depth = SUITCASE["dimensions"]
    _c, packer_items, _cfg, _w = load_scenario(
        {"container": {"id": "c", "dims": [width, depth, height]}, "items": prepare_items(ITEMS)})
    dims = {it.id: {"x": it.dims[0], "y": it.dims[2], "z": it.dims[1]} for it in packer_items}

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    out.with_name("item_dims.json").write_text(json.dumps(dims, indent=2))
    return len(doc["plan"]["placements"])


if __name__ == "__main__":
    print(main(Path(sys.argv[1])))
