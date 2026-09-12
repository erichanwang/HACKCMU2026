"""Physics first, then several solver runs, then physics picks the plan.

1. `physics.prepack.prepare_items` decides per item what may be packed (compressed height,
   fragile, keep upright, mass) before the solver sees it.
2. packer3d packs those items several ways (`CANDIDATES`: first-fit plus optimised runs
   with different seeds, sharing `TIME_BUDGET_S`).
3. `physics.packer3d_adapter.validate_packer3d` grades every candidate; `rank` orders them
   (valid first, then fewest violations, most items packed, physics score, utilisation,
   smallest centre-of-mass offset) and the best becomes the plan. All of them are kept
   in `alternatives` so the UI can say what was tried.

`packer3d/` and `physics/` live at the repo root, not in this uv project, so they are put
on `sys.path` the same way `physics/packer3d_adapter.py::_load_scenario` does it.
"""
import json
import logging
import os
import sys
import threading
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / "packer3d"):  # repo root for `physics`, packer3d/ for the `packer3d` package
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

from packer3d import OptimizerConfig, pack_naive, pack_optimized  # noqa: E402
from packer3d.scenario import load_scenario  # noqa: E402
from physics.packer3d_adapter import validate_packer3d  # noqa: E402
from physics.prepack import prepare_items  # noqa: E402

from app_plan import to_app_plan  # noqa: E402

logger = logging.getLogger("suitcase")

TIME_BUDGET_S = float(os.environ.get("PLAN_TIME_BUDGET_S", 3.0))  # shared by the optimised candidates
CANDIDATES = (("naive", None), ("optimized", 0), ("optimized", 1), ("optimized", 2))
_lock = threading.Lock()  # ponytail: one global lock; per-suitcase locks if a booth ever runs two bags


def rank(candidate: dict) -> tuple:
    """Sort key, higher is better: physics verdict first, solver metrics after."""
    v, m = candidate["validation"], candidate["solver"]["metrics"]
    return (bool(v["valid"]), -len(v["violations"]), m["items_packed"], v["score"],
            m["volume_utilization"], -m["com_lateral_offset"])


def summary(candidate: dict) -> dict:
    v, m = candidate["validation"], candidate["solver"]["metrics"]
    return {"strategy": candidate["strategy"], "seed": candidate["seed"],
            "items_packed": m["items_packed"], "volume_utilization": m["volume_utilization"],
            "com_lateral_offset": m["com_lateral_offset"],
            "physics_valid": v["valid"], "violations": len(v["violations"]), "physics_score": v["score"]}


def plan(suitcase: dict, items: list[dict]) -> dict:
    """`{"solver", "validation", "plan", "chosen", "alternatives", "unpacked", "runnerUp"}` for one suitcase and its item documents."""
    width, height, depth = (float(v) for v in suitcase["dimensions"])
    # suitcase dimensions are [width, height, depth] (app frame, Y up); packer3d's container
    # is (x = length, y = width, z = up) -> [width, depth, height].
    scenario = {"container": {"id": str(suitcase["_id"]), "dims": [width, depth, height]},
                "items": prepare_items(items)}
    container, packer_items, _config, _weights = load_scenario(scenario)
    per_run = TIME_BUDGET_S / sum(1 for s, _ in CANDIDATES if s == "optimized")
    candidates = []
    total_start = time.perf_counter()
    with _lock:
        for strategy, seed in CANDIDATES:
            candidate_start = time.perf_counter()
            if strategy == "naive":
                result = pack_naive(container, packer_items)
            else:
                result = pack_optimized(container, packer_items, config=OptimizerConfig(time_budget_s=per_run, seed=seed))
            # to_json rather than to_dict: the metrics carry numpy scalars, which pymongo cannot store
            result_dict = json.loads(result.to_json())
            validation = validate_packer3d(result_dict, items=scenario["items"])
            logger.info("candidate strategy=%s seed=%s seconds=%.3f items_packed=%d physics_valid=%s",
                        strategy, seed, time.perf_counter() - candidate_start,
                        result_dict["metrics"]["items_packed"], validation["valid"])
            candidates.append({"strategy": strategy, "seed": seed, "solver": result_dict, "validation": validation})
    logger.info("plan total seconds=%.3f candidates=%d", time.perf_counter() - total_start, len(candidates))
    candidates.sort(key=rank, reverse=True)
    items_by_id = {str(i["id"]): i for i in items}

    def full(c: dict) -> dict:
        return {"solver": c["solver"], "validation": c["validation"],
                "plan": to_app_plan(c["solver"], suitcase, items_by_id),
                "chosen": {"strategy": c["strategy"], "seed": c["seed"]}}

    best = full(candidates[0])
    doc = {**best,
           "alternatives": [summary(c) for c in candidates],
           # top-level, NOT inside "plan": that nested object is the iOS PackingPlan contract and must not change
           "unpacked": [{"itemId": u["id"], "label": items_by_id.get(u["id"], {}).get("label") or u["id"]}
                        for u in best["solver"]["unpacked"]]}
    if len(candidates) > 1:
        doc["runnerUp"] = full(candidates[1])
    return doc
