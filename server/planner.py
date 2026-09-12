"""Run the packing solver and the physics validator on a suitcase's scanned items.

`packer3d/` and `physics/` live at the repo root, not in this uv project, so they are put
on `sys.path` the same way `physics/packer3d_adapter.py::_load_scenario` does it.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "packer3d"):  # repo root for `physics`, packer3d/ for the `packer3d` package
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

from packer3d import OptimizerConfig, pack_optimized  # noqa: E402
from packer3d.scenario import load_scenario  # noqa: E402
from physics.packer3d_adapter import validate_packer3d  # noqa: E402

from app_plan import to_app_plan  # noqa: E402

TIME_BUDGET_S = 3.0


def plan(suitcase: dict, items: list[dict]) -> dict:
    """`{"solver", "validation", "plan"}` for one suitcase and its item documents."""
    width, height, depth = (float(v) for v in suitcase["dimensions"])
    # suitcase dimensions are [width, height, depth] (app frame, Y up); packer3d's container
    # is (x = length, y = width, z = up) -> [width, depth, height].
    scenario = {"container": {"id": str(suitcase["_id"]), "dims": [width, depth, height]}, "items": items}
    container, packer_items, _config, _weights = load_scenario(scenario)
    result = pack_optimized(container, packer_items, config=OptimizerConfig(time_budget_s=TIME_BUDGET_S, seed=0))
    # to_json rather than to_dict: the metrics carry numpy scalars, which pymongo cannot store
    result_dict = json.loads(result.to_json())
    return {
        "solver": result_dict,
        "validation": validate_packer3d(result_dict, items=scenario["items"]),
        "plan": to_app_plan(result_dict, suitcase, {str(i["id"]): i for i in items}),
    }
