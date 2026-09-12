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


def bowl_scan(item_id, w, h, d, rim=0.03, floor=0.01, **over):
    """An open box seen from above: a `rim`-wide wall at full height around a low interior, so
    `Item.from_scanned_heightmap` carves a real cavity and the decoder may nest a small item in it."""
    cell = 0.01
    rows, cols = math.ceil(w / cell), math.ceil(d / cell)
    r = math.ceil(rim / cell)
    heights = [[h if (i < r or j < r or i >= rows - r or j >= cols - r) else floor for j in range(cols)]
               for i in range(rows)]
    return scan(item_id, w, h, d, **over) | {"cellSize": cell, "heights": heights}


SUITCASE = {"_id": "contract-carry-on", "name": "Contract carry-on", "dimensions": [0.4, 0.2, 0.3]}
ITEMS = [
    scan("shirts", 0.3, 0.1, 0.2, rigidity="soft", compressibility=2.0, label="Folded shirts"),
    scan("bottle", 0.07, 0.18, 0.07, keepUpright=True, label="Water bottle"),
    scan("camera", 0.15, 0.08, 0.1, rigidity="fragile", label="Camera"),
    scan("book", 0.15, 0.04, 0.2, label="Paperback"),
    # taller than the bag: it can only fit lying down, which exercises the cylinder rotations
    round_scan("longbottle", 0.18, 0.35, label="Tall bottle"),
    # an open-top box with a real cavity and something small that fits in it: if the decoder
    # nests them, the plan carries nestedIn + cavity and the Swift side must accept the overlap
    bowl_scan("bowl", 0.22, 0.08, 0.22, label="Open box"),
    scan("socks", 0.1, 0.04, 0.1, rigidity="soft", compressibility=1.5, label="Socks"),
]


# A bag the open box fills wall to wall, 2 cm shorter than box + socks stacked: the socks fit
# nowhere except inside the box's cavity, so the plan must carry nestedIn or leave them out.
NEST_SUITCASE = {"_id": "contract-nest", "name": "Contract nest", "dimensions": [0.24, 0.10, 0.24]}
NEST_ITEMS = [
    bowl_scan("bowl", 0.22, 0.08, 0.22, label="Open box"),
    scan("socks", 0.1, 0.04, 0.1, rigidity="soft", compressibility=1.5, label="Socks"),
]


def write(suitcase: dict, items: list, out: Path, dims_name: str) -> int:
    planner.TIME_BUDGET_S = 0.3
    doc = {"suitcaseId": str(suitcase["_id"]),
           "createdAt": datetime.now(timezone.utc).isoformat()} | planner.plan(suitcase, items)

    # The solver's own view of each item, for the `size` vs `rotation` check. packer3d's frame
    # is (x = length, y = width, z = up), so app (x, y, z) = (dims[0], dims[2], dims[1]).
    width, height, depth = suitcase["dimensions"]
    _c, packer_items, _cfg, _w = load_scenario(
        {"container": {"id": "c", "dims": [width, depth, height]}, "items": prepare_items(items)})
    dims = {it.id: {"x": it.dims[0], "y": it.dims[2], "z": it.dims[1]} for it in packer_items}

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2))
    out.with_name(dims_name).write_text(json.dumps(dims, indent=2))
    return len(doc["plan"]["placements"])


def main(out: Path) -> int:
    n = write(SUITCASE, ITEMS, out, "item_dims.json")
    write(NEST_SUITCASE, NEST_ITEMS, out.with_name("nest_doc.json"), "nest_dims.json")
    return n


if __name__ == "__main__":
    print(main(Path(sys.argv[1])))
