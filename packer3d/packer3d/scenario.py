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
    if "id" not in d:
        raise ValueError(f"item entry is missing required key 'id': {d!r}")
    shape = d.get("shape", "box")
    mass = d.get("mass", 0.0)
    priority = d.get("priority", 1.0)
    # fragile/keep_upright are passed through only when explicitly present; otherwise None, so
    # from_scan/from_scanned_heightmap's own "irregular defaults to fragile+upright" logic applies
    # even when the scenario comes from JSON (a definite bool here would silently override it).
    fragile = d["fragile"] if "fragile" in d else None
    keep_upright = d["keep_upright"] if "keep_upright" in d else None
    count = int(d.get("count", 1))
    if count < 1:
        raise ValueError(f"item {d['id']!r}: count must be >= 1, got {count}")
    ids = [d["id"]] if count == 1 else [f"{d['id']}_{k + 1}" for k in range(count)]
    out = []
    for iid in ids:
        if "heights" in d:  # lidar spike heightmap payload (SCAN_OUTPUT.md)
            out.append(Item.from_scanned_heightmap(d, mass=mass, fragile=fragile, keep_upright=keep_upright,
                                                    priority=priority))
            continue
        if "length" in d:  # lidar payload: length / depth / height + shape
            if "depth" not in d or "height" not in d:
                raise ValueError(f"item {iid!r}: 'length' payload also needs 'depth' and 'height'")
            out.append(Item.from_scan(iid, shape, d["length"], d["depth"], d["height"], mass,
                                      fragile=fragile, keep_upright=keep_upright,
                                      allow_lay_down=bool(d.get("allow_lay_down", True)), priority=priority))
            continue
        common = dict(mass=mass, fragile=bool(fragile), keep_upright=bool(keep_upright), priority=priority)
        if shape == "box":
            if "dims" not in d:
                raise ValueError(f"item {iid!r}: shape 'box' needs a 'dims' key")
            dims = d["dims"]
            out.append(Item.box(iid, dims[0], dims[1], dims[2], **common))
        elif shape == "cylinder":
            if "radius" not in d or "height" not in d:
                raise ValueError(f"item {iid!r}: shape 'cylinder' needs 'radius' and 'height' keys")
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
    if "container" not in data:
        raise ValueError("scenario is missing required top-level key 'container'")
    c = data["container"]
    if "dims" not in c:
        raise ValueError("scenario container is missing required key 'dims'")
    max_mass = c.get("max_mass", None)
    obstacles = []
    for o in c.get("obstacles", []):
        for key in ("id", "position", "dims"):
            if key not in o:
                raise ValueError(f"container obstacle entry is missing required key {key!r}: {o!r}")
        obstacles.append(Obstacle(o["id"], tuple(o["position"]), tuple(o["dims"])))
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
