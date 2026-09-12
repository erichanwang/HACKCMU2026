"""Load a scenario (container + items + optimiser settings) from JSON.

Schema (all keys except ``container.dims`` and ``items`` optional):
{
  "container": {"id", "shape": "box|cylinder", "dims": [L,W,H], "gravity": true, "max_mass": 23,
                "min_support": 0.7, "com_target": [x,y,z], "com_axis_weights": [1,1,0.5],
                "obstacles": [{"id", "position": [x,y,z], "dims": [dx,dy,dz]}]},
  "items": [{"id", "shape": "box", "dims": [l,w,h], "mass", "fragile", "keep_upright", "priority", "count"},
            {"id", "shape": "box|cylinder", "length", "depth", "height", ...}   <- lidar payload form
            {"id", "shape": "cylinder", "radius", "height", "mass", "keep_upright", "allow_lay_down", ...}],
  "optimizer": {"time_budget_s": 5, "max_iterations": null, "seed": 0},
  "weights": {"unpacked": 10, "compact": 1, "com": 6, "height": 0.5}
}
``count`` > 1 expands an item into ``id_1 .. id_n`` copies (handy for lidar-scanned batches).
"""
from __future__ import annotations

import json
import math

from .models import Container, Item, Obstacle
from .objective import ObjectiveWeights
from .search import OptimizerConfig


def _item_from_dict(d: dict) -> list:
    shape = d.get("shape", "box")
    common = dict(mass=d.get("mass", 0.0), fragile=bool(d.get("fragile", False)),
                  keep_upright=bool(d.get("keep_upright", False)), priority=d.get("priority", 1.0))
    count = int(d.get("count", 1))
    ids = [d["id"]] if count == 1 else [f"{d['id']}_{k + 1}" for k in range(count)]
    out = []
    for iid in ids:
        if "length" in d:  # lidar payload: length / depth / height + shape
            out.append(Item.from_scan(iid, shape, d["length"], d["depth"], d["height"],
                                      allow_lay_down=bool(d.get("allow_lay_down", True)), **common))
            continue
        if shape == "box":
            dims = d["dims"]
            out.append(Item.box(iid, dims[0], dims[1], dims[2], **common))
        elif shape == "cylinder":
            out.append(Item.cylinder(iid, d["radius"], d["height"],
                                     allow_lay_down=bool(d.get("allow_lay_down", True)), **common))
        else:
            raise ValueError(f"unknown item shape {shape!r}")
    return out


def load_scenario(src):
    """``src`` = path, JSON string or dict. Returns (container, items, OptimizerConfig, ObjectiveWeights)."""
    if isinstance(src, dict):
        data = src
    else:
        text = src
        try:
            with open(src, "r") as fh:
                text = fh.read()
        except (OSError, TypeError):
            pass
        data = json.loads(text)
    c = data["container"]
    max_mass = c.get("max_mass", None)
    obstacles = [Obstacle(o["id"], tuple(o["position"]), tuple(o["dims"])) for o in c.get("obstacles", [])]
    container = Container(
        id=c.get("id", "container"), dims=tuple(c["dims"]), shape=c.get("shape", "box"),
        gravity=bool(c.get("gravity", True)), max_mass=math.inf if max_mass is None else float(max_mass),
        obstacles=obstacles, min_support=float(c.get("min_support", 0.7)),
        com_target=tuple(c["com_target"]) if c.get("com_target") else None,
        com_axis_weights=tuple(c["com_axis_weights"]) if c.get("com_axis_weights") else None,
    )
    items = []
    for d in data.get("items", []):
        items.extend(_item_from_dict(d))
    opt = data.get("optimizer", {})
    config = OptimizerConfig(**{k: v for k, v in opt.items() if k in OptimizerConfig.__dataclass_fields__})
    w = data.get("weights", {})
    weights = ObjectiveWeights(**{k: v for k, v in w.items() if k in ObjectiveWeights.__dataclass_fields__})
    return container, items, config, weights
