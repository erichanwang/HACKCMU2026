"""Load a scenario (container + items + optimiser settings) from JSON.

Schema (all keys except ``container.dims`` and ``items`` optional):
{
  "container": {"id", "shape": "box|cylinder", "dims": [L,W,H], "gravity": true, "max_mass": 23,
                "min_support": 0.7, "com_target": [x,y,z], "com_axis_weights": [1,1,0.5],
                "obstacles": [{"id", "position": [x,y,z], "dims": [dx,dy,dz]}],
                "profile": "two_wheel|spinner",      <- add the wheel wells / handle rails a bag
                "profile_fractions": {...}},            of that type has (see SUITCASE_PROFILES)
  "zones": [{"id", "label", "origin": [x,y,z], "size": [dx,dy,dz],
             "gravity", "min_support", "max_mass"}],  <- sub-volumes, see load_zones()
  "items": [{"id", "shape": "box", "dims": [l,w,h], "mass", "fragile", "keep_upright", "priority", "count",
             "zone": "lid_pocket",                    <- which zone may hold it (default: the first)
             "rigidity": "soft", "compressibility": 2.0},   <- soft items pack at height / k (Item.compressed);
                                                              "rigidity": "fragile" / "keepUpright" (server docs) also work
            {"id", "shape": "box|cylinder", "length", "depth", "height", ...}   <- lidar payload form
            {"id", "shape": "cylinder", "radius", "height", "mass", "keep_upright", "allow_lay_down", ...},
            {"id", "width", "depth", "height", "cellSize", "heights": [[...]]}      <- heightmap, CENTIMETRES
            {"id", "dimensions": [w, h, d], "cellSize", "heights": [[...]]}],      <- heightmap, METRES
  "optimizer": {"time_budget_s": 5, "max_iterations": null, "seed": 0},
  "weights": {"unpacked": 10, "compact": 1, "com": 6, "height": 0.5}
}
``count`` > 1 expands an item into ``id_1 .. id_n`` copies (handy for lidar-scanned batches).
Every key added here is optional: a scenario written before them loads exactly as it did.

WATCH THE UNITS.  Every length here is metres -- except a ``heights`` payload spelled with
``width``/``depth``/``height``, which ``Item.from_scanned_heightmap`` reads as CENTIMETRES
(its own default; ``tools/packbench/fixtures`` relies on it).  The server document form,
``dimensions: [width, height, depth]``, is metres.  The phone only ever sends the latter, so
a hand-written scenario is the only way to mix the two -- and mixing them is a silent 100x.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass

from .geometry import EPS
from .models import Container, Item, Obstacle
from .objective import ObjectiveWeights
from .search import OptimizerConfig

# ---------------------------------------------------------------- suitcase interior profiles
# A scanned interior box is not the packable volume.  The wheel housings bulge into the floor
# corners and the telescoping handle leaves a raised channel down the middle of the panel you
# pack against, so a plan that assumes an empty rectangle is optimistic -- and will happily
# tell someone to put a hard camera case exactly where the handle rails are.
#
# Frame, the one server/planner.py hands us for a suitcase lying open (x = length, y = width,
# z = up, origin at the min corner):
#   x = across the bag (the wheel axle direction), y = wheel end -> handle-grip end,
#   z = up from the panel you are packing against.
#
# THESE ARE ESTIMATES, not measurements of your bag.  They are fractions of the scanned
# interior so they scale, and "profile_fractions" overrides any of them.  Where they come
# from, sized against a 55 x 40 x 23 cm IATA cabin bag:
#
#   wheel_well  ~10 cm (along the bag) x 6 cm (across) x 5 cm (up) per rear floor corner.
#               Replacement spinner casters are sold at 48-50 mm wheel diameter, 10 mm wheel
#               width, 33 x 33 mm mounting-plate hole pitch, 80 mm assembly height over 60 mm
#               ground clearance -- so ~20 mm of assembly, plus the moulded pocket and the
#               reinforcement around it, sits inside the shell line.  A 2-wheel bag instead
#               half-buries a 60-70 mm inline wheel.  Both land near 10 x 6 x 5, which matches
#               the corner bumps you can feel in an empty hardside carry-on.
#               -> (6/40, 10/55, 5/23) = (0.15, 0.18, 0.22).
#   handle_rails  two 18-20 mm tubes at ~30 cm centres with a moulded fairing bridging them:
#               ~28 cm across, ~2.5 cm proud of the panel, running ~85% of the bag's length.
#               -> (28/40, 0.85, 2.5/23) = (0.70, 0.85, 0.11).  Bags with the handle mounted
#               outside the shell have none of this: pass {"handle_rails": None}.
#
# Sanity check: the two wells are only ~1.2% of the box, the ridge ~6.5%.  So the wells cost
# almost no volume and cost you the two most useful floor corners, which is the point.  The
# rest of the 10-15% OVERVIEW.md claims is wall curvature and tapered corners, which an
# axis-aligned obstacle cannot express -- treat ~8% as the floor of the correction, not the
# ceiling.  (Independent smell test: a Rimowa Classic Cabin is 55 x 40 x 23 cm = 50.6 L
# outside and sells as 36 L inside; most of that gap is shell thickness, which the LiDAR scan
# already sees, but wheels and handle are in there too.)
# WHICH end is the wheel end is this module's own convention, not a measured fact. A single-tap
# LiDAR scan carries no signal for which real side of the bag the frame's origin lands on
# (docs/AR_BUILD.md, "Which physical wall is back?"), so a profile places its wheel wells and
# handle rails at *an* end, not necessarily the end they are on in the bag being scanned. Getting
# it backwards reserves space at the lid end and leaves the real wheel wells packable, which is
# worse than modelling nothing. That is survivable only because profiles are opt-in and nothing
# on the server path sets `container.profile`; do not turn them on for a real scan until the app
# captures a lid or wheel cue and the scan says which end is which.
SUITCASE_PROFILES = {
    # two wheels at the rear floor corners, handle rails inside the shell (classic carry-on)
    "two_wheel": {"wheel_well": (0.15, 0.18, 0.22), "wheel_corners": 2,
                  "handle_rails": (0.70, 0.85, 0.11)},
    # four externally-mounted casters: shallower (the wheel itself is outside), but all four
    # floor corners go.  ~8 x 8 cm plate and reinforcement, ~3.5 cm proud.
    "spinner": {"wheel_well": (0.20, 0.15, 0.15), "wheel_corners": 4,
                "handle_rails": (0.70, 0.85, 0.11)},
}


def suitcase_obstacles(dims, profile: str = "two_wheel", fractions: dict = None) -> list:
    """The obstacles a bag of type ``profile`` actually has, scaled to interior ``dims``.

    ``fractions`` overrides any key of the profile -- ``{"wheel_well": (0.12, 0.14, 0.18)}``
    for a bag you have measured, ``{"handle_rails": None}`` for an external handle.
    """
    if profile not in SUITCASE_PROFILES:
        raise ValueError(f"unknown suitcase profile {profile!r}; known: {sorted(SUITCASE_PROFILES)}")
    spec = dict(SUITCASE_PROFILES[profile], **(fractions or {}))
    lx, ly, lz = (float(v) for v in dims)
    out, well_dx = [], 0.0
    f = spec.get("wheel_well")
    if f:
        well_dx, dy, dz = f[0] * lx, f[1] * ly, f[2] * lz
        corners = [(0.0, 0.0), (lx - well_dx, 0.0)]
        if int(spec.get("wheel_corners", 2)) == 4:
            corners += [(0.0, ly - dy), (lx - well_dx, ly - dy)]
        for n, (x, y) in enumerate(corners, 1):
            out.append(Obstacle(f"wheel_well_{n}", (x, y, 0.0), (well_dx, dy, dz)))
    f = spec.get("handle_rails")
    if f:
        # the rails pass between the wheel housings, never through them: overlapping obstacles
        # would be double-counted by Container.usable_volume.
        dx = min(f[0] * lx, lx - 2 * well_dx)
        if dx > EPS:
            out.append(Obstacle("handle_rails", ((lx - dx) / 2.0, 0.0, 0.0), (dx, f[1] * ly, f[2] * lz)))
    return out


# ------------------------------------------------------------------------------------ zones
@dataclass(frozen=True)
class Zone:
    """A named sub-volume of the container, in container coordinates.

    Same shape as the ``zones`` array in the app's plan JSON (id, label, origin, size).  A zone
    is allowed to sit outside the container box -- a lid pocket is in the other shell half.
    """
    id: str
    label: str
    origin: tuple
    size: tuple


def _item_from_dict(d: dict) -> list:
    if "id" not in d:
        raise ValueError(f"item entry is missing required key 'id': {d!r}")
    shape = d.get("shape", "box")
    mass = d.get("mass", 0.0)
    priority = d.get("priority", 1.0)
    # fragile/keep_upright are passed through only when explicitly present; otherwise None, so
    # from_scan/from_scanned_heightmap's own "irregular defaults to fragile+upright" logic applies
    # even when the scenario comes from JSON (a definite bool here would silently override it).
    # Server documents (SCAN_OUTPUT.md) say `rigidity: "fragile"` and `keepUpright` instead.
    fragile = d["fragile"] if "fragile" in d else (True if d.get("rigidity") == "fragile" else None)
    keep_upright = d["keep_upright"] if "keep_upright" in d else d.get("keepUpright")
    count = int(d.get("count", 1))
    if count < 1:
        raise ValueError(f"item {d['id']!r}: count must be >= 1, got {count}")
    # compressibility k (SCAN_OUTPUT.md) only means something for soft items; a stray k on a rigid one is ignored.
    comp_k = float(d.get("compressibility", 1.0)) if d.get("rigidity", "soft") == "soft" else 1.0
    ids = [d["id"]] if count == 1 else [f"{d['id']}_{k + 1}" for k in range(count)]
    out = []
    for iid in ids:
        if "heights" in d:  # lidar spike heightmap payload (SCAN_OUTPUT.md)
            # from_scanned_heightmap takes the id off the payload, so `count` > 1 has to hand it
            # the generated `iid` -- passing `d` untouched gave every copy the same id and
            # validate_items rejected the lot, the way the "length" branch below never did.
            out.append(Item.from_scanned_heightmap({**d, "id": iid}, mass=mass, fragile=fragile,
                                                    keep_upright=keep_upright,
                                                    allow_lay_down=bool(d.get("allow_lay_down", True)),
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
            if len(dims) != 3:  # a 4th entry was silently dropped; 2 entries raised IndexError
                raise ValueError(f"item {iid!r}: 'dims' must have exactly 3 entries, got {dims!r}")
            out.append(Item.box(iid, dims[0], dims[1], dims[2], **common))
        elif shape == "cylinder":
            if "radius" not in d or "height" not in d:
                raise ValueError(f"item {iid!r}: shape 'cylinder' needs 'radius' and 'height' keys")
            out.append(Item.cylinder(iid, d["radius"], d["height"],
                                     allow_lay_down=bool(d.get("allow_lay_down", True)), **common))
        else:
            raise ValueError(f"unknown item shape {shape!r}")
    return [it.compressed(comp_k) for it in out]


def _read(src) -> dict:
    """``src`` = path, JSON string or dict -> the scenario dict."""
    if isinstance(src, dict):
        return src
    text = src
    try:
        with open(src, "r") as fh:
            text = fh.read()
    except (OSError, TypeError):
        pass
    return json.loads(text)


def _container_from_dict(data: dict) -> Container:
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
    if c.get("profile"):
        obstacles += suitcase_obstacles(c["dims"], c["profile"], c.get("profile_fractions"))
    return Container(
        id=c.get("id", "container"), dims=tuple(c["dims"]), shape=c.get("shape", "box"),
        gravity=bool(c.get("gravity", True)), max_mass=math.inf if max_mass is None else float(max_mass),
        obstacles=obstacles, min_support=float(c.get("min_support", 0.7)),
        com_target=tuple(c["com_target"]) if c.get("com_target") else None,
        com_axis_weights=tuple(c["com_axis_weights"]) if c.get("com_axis_weights") else None,
    )


def _config_weights(data: dict) -> tuple:
    opt = data.get("optimizer", {})
    config = OptimizerConfig(**{k: v for k, v in opt.items() if k in OptimizerConfig.__dataclass_fields__})
    w = data.get("weights", {})
    weights = ObjectiveWeights(**{k: v for k, v in w.items() if k in ObjectiveWeights.__dataclass_fields__})
    return config, weights


def load_scenario(src):
    """``src`` = path, JSON string or dict. Returns (container, items, OptimizerConfig, ObjectiveWeights).

    Zone-blind: the container is the whole interior and ``items`` is every item, whatever zone
    it asked for. Use :func:`load_zones` to pack zone by zone.
    """
    data = _read(src)
    container = _container_from_dict(data)
    items = []
    for d in data.get("items", []):
        items.extend(_item_from_dict(d))
    config, weights = _config_weights(data)
    return container, items, config, weights


def _zone_container(base: Container, zone: Zone, spec: dict) -> Container:
    """``base`` restricted to ``zone``: the parent's settings unless the zone overrides them,
    and the parent's obstacles clipped to the zone's box (a lid pocket keeps none of them)."""
    obstacles = []
    for ob in base.obstacles:
        pos, dims = [], []
        for k in range(3):
            lo = max(ob.position[k] - zone.origin[k], 0.0)
            hi = min(ob.position[k] + ob.dims[k] - zone.origin[k], zone.size[k])
            pos.append(lo)
            dims.append(hi - lo)
        if all(d > EPS for d in dims):
            obstacles.append(Obstacle(ob.id, tuple(pos), tuple(dims)))
    max_mass = spec.get("max_mass", base.max_mass)
    return Container(
        id=f"{base.id}:{zone.id}", dims=zone.size, shape=base.shape,
        gravity=bool(spec.get("gravity", base.gravity)),
        max_mass=math.inf if max_mass is None else float(max_mass),
        obstacles=obstacles, min_support=float(spec.get("min_support", base.min_support)),
        # dimensionless axis priorities, so they carry into a zone unchanged -- dropping them
        # silently reverted a scenario's declared weights to the (1, 1, 0.5) default per zone.
        com_axis_weights=base.com_axis_weights,
        # com_target deliberately does NOT carry: it is an absolute point in the parent's frame,
        # so each zone balances about its own centre (Container.effective_com_target) instead.
    )


def load_zones(src):
    """``([(Zone, Container, [Item]), ...], OptimizerConfig, ObjectiveWeights)`` -- one packing
    problem per declared zone, in declaration order.

    A scenario with no ``"zones"`` key -- every scenario written so far -- gets a single
    ``interior`` zone covering the whole container and holding every item, which is exactly
    what :func:`load_scenario` returns.  Declare more and each item goes to the zone named by
    its ``"zone"`` key, defaulting to the first zone::

        "zones": [{"id": "interior", "label": "Interior"},
                  {"id": "lid_pocket", "label": "Lid pocket", "origin": [0, 0, 0.23],
                   "size": [0.34, 0.50, 0.05], "gravity": false, "min_support": 0.0}],
        "items": [{"id": "map", "shape": "box", "dims": [0.2, 0.3, 0.01], "zone": "lid_pocket"}]

    Nothing lands in the lid pocket that did not ask for it, and the pocket's own depth is what
    keeps anything but flat things out -- no separate "flat items only" rule to keep in sync.
    """
    data = _read(src)
    base = _container_from_dict(data)
    config, weights = _config_weights(data)
    zones = []
    for z in data.get("zones") or [{"id": "interior", "label": "Interior"}]:
        if "id" not in z:
            raise ValueError(f"zone entry is missing required key 'id': {z!r}")
        zone = Zone(str(z["id"]), str(z.get("label", z["id"])),
                    tuple(float(v) for v in z.get("origin", (0.0, 0.0, 0.0))),
                    tuple(float(v) for v in z.get("size", base.dims)))
        if any(zone.id == other.id for other, _ in zones):
            raise ValueError(f"duplicate zone id {zone.id!r}")
        zones.append((zone, _zone_container(base, zone, z)))
    buckets = {zone.id: [] for zone, _ in zones}
    default_id = zones[0][0].id
    for d in data.get("items", []):
        zid = str(d.get("zone", default_id))
        if zid not in buckets:
            raise ValueError(f"item {d.get('id')!r} asks for zone {zid!r}, which the scenario does not declare")
        buckets[zid].extend(_item_from_dict(d))
    return [(zone, container, buckets[zone.id]) for zone, container in zones], config, weights
