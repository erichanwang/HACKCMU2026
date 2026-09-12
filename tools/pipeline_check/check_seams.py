#!/usr/bin/env python3
"""End-to-end seam check: fixture scan documents -> server plan JSON -> the Swift decoder.

Every check we otherwise have covers ONE stage. This one drives the REAL chain, offline
(no Mongo, no FastAPI), and asserts the things that only break BETWEEN stages:

    fixture scan docs (SCAN_OUTPUT.md shape)
      -> physics.prepack.prepare_items        (compressed height, fragile, keep upright)
      -> packer3d                             (packs; frame x=length, y=width, z=up)
      -> physics.packer3d_adapter             (grades the layout)
      -> server/app_plan.to_app_plan          (bag frame x=width, y=up, z=depth; rotation strings)
      -> tools/plan3d                         (PackingPlan.PlanLoader + geometryIssues())

Nothing here reimplements a stage: `server/planner.plan()` is called as the server calls it,
and the plan is handed to the built Swift binary exactly as the app would receive it.

Run (from the repo root):

    python3 tools/pipeline_check/check_seams.py

The Swift decoder stage needs the plan3d binary. Build it once:

    PATH="$HOME/.local/share/swiftly/bin:$PATH" \
    LD_LIBRARY_PATH="$HOME/.local/swift-compat/usr/lib/x86_64-linux-gnu" \
    swift build --package-path tools/plan3d

and/or point `PLAN3D_BIN` at it. A missing binary is a FAILURE, not a skip.

Determinism: `plan()` runs all four of its candidates, but the optimiser's stopping rule is
swapped from "spend N seconds" to "run N iterations" (`OPTIMIZER_ITERATIONS`, the knob
`packer3d.OptimizerConfig` already has), so the same machine-independent plan comes out every
time. Nothing else about the run changes: same solver, same ranking, same chosen candidate.

Proving it can fail: `--perturb {escape,rotation,overlap,nest,nest_outside,step,height}` corrupts the plan
after planning, in memory, so each assertion can be seen catching its own seam. `--perturb`
touches the main fixture only; the three cavity fixtures below always run as planned.

Four fixtures, because the `nestedIn` cavity seam needs a plan that actually has a cavity:

  ITEMS           the general fixture: compression, cylinders, keepUpright, an unpacked item.
                  No nesting, so the nestedIn-shape assertion reports N/A on it -- not PASS.
  NEST_ITEMS      a rigid case with a foam recess and a lens that fits only inside it. The real
                  solver nests here, so the whole chain is asserted against genuine solver
                  output: packer3d's `nested_in`, the y/z swap into `nestedIn`, the pair's
                  shared volume against the declared cavity, the Swift decoder (silent on the
                  nested pair, still loud on the same overlap undeclared), and the verdict.
  MULTI_ITEMS     the same case with TWO foam cut-outs and two lenses, one per cut-out. The only
                  fixture where a host has more than one cavity, which is the one shape that can
                  tell "this overlap is inside the guest's own cell" apart from "this overlap is
                  somewhere inside the host" -- see check_multi_cavity.
  NEST_GAP_ITEMS  the same seam with an open box, the shape a bowl or a shoe really scans as.
                  Reported as a GAP, with the diagnosis, and the assertions above engage on it
                  automatically the day it nests. See below.

KNOWN GAP, as of 2026-09-12: an open-topped scan never reaches the cavity path at all, and this
is not the solver declining a cavity it could use. A shape with a genuine cavity has true volume
/ bbox < 0.9, so `Item.from_scanned_heightmap` classifies it `irregular`, which defaults it to
`fragile=True` ("its top is not flat, don't stack on it") -- and `packer3d/decoder.py` rejects
any candidate whose base rests on a fragile solid, which the cavity's own floor is. So the one
place resting is intended is the one place it is forbidden. `physics/prepack.packable` only ever
*sets* `fragile`, never clears it, so no SCAN_OUTPUT document can get past it. Verified by
flipping that single flag on the open-box fixture: the solver then nests it immediately. The
recessed-case fixture nests today only because its dip is 5% of the bounding volume, which keeps
it classified `box`. The fix is in packer3d/physics, not in this directory.

A second, quieter trap the fixtures have to respect: `Item.solid_boxes` max-pools `heights` to
4 x 4 blocks, taking each block's TALLEST cell. A cavity that is not a whole number of pooled
blocks is pooled away and the item has no cavity at all (see `recess_grid`).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for _p in (ROOT, ROOT / "packer3d", ROOT / "server"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import planner  # noqa: E402  server/planner.py -- the real entry point the POST /plan route uses
from packer3d.models import oriented_solid_boxes  # noqa: E402
from packer3d.scenario import load_scenario  # noqa: E402
from physics.compressibility import compression_allowance_m  # noqa: E402
from physics.constraints import RESTING_CONTACT_EPS_M  # noqa: E402
from physics.packer3d_adapter import scene_from_packer3d, swap_yz  # noqa: E402
from physics.prepack import physics_object, prepare_items  # noqa: E402
from physics.schema import Constraints, Container, Object, Scene  # noqa: E402
from physics.validator import validate_layout  # noqa: E402

OPTIMIZER_ITERATIONS = 40   # SA iterations per optimised candidate, in place of a wall-clock budget
EPS = 1e-6          # geometry slack: the solver works in metres, plans are printed as floats
# 1e-3 m, the physics layer's OWN resting-contact tolerance ("is this object resting on that one",
# physics/support.py). Deliberately coarser than EPS: it absorbs reconstruction noise between two
# independently placed faces, not just float64 rounding. Support is asked with this one so this
# file and the grader read the same resting graph.
CONTACT = RESTING_CONTACT_EPS_M
AXIS = ("x", "y", "z")
LETTER = {"X": 0, "Y": 1, "Z": 2}


# ----------------------------------------------------------------- fixture scan documents
# Shaped like a real phone scan (SCAN_OUTPUT.md): dimensions [width, height, depth] in
# metres, a 1 cm `heights` grid, rigidity / compressibility / mass / keepUpright.
def grid(w: float, h: float, d: float, cell: float = 0.01, round_footprint: bool = False) -> list[list[float]]:
    """`heights`: rows along width, columns along depth. `round_footprint` carves the
    circular footprint a bottle scans as, so packer3d classifies it as a cylinder."""
    rows, cols = math.ceil(w / cell), math.ceil(d / cell)
    if not round_footprint:
        return [[h] * cols for _ in range(rows)]
    r = min(w, d) / 2.0
    out = []
    for i in range(rows):
        row = []
        for j in range(cols):
            cx, cy = (i + 0.5) * cell - w / 2.0, (j + 0.5) * cell - d / 2.0
            row.append(h if cx * cx + cy * cy <= r * r else 0.0)
        out.append(row)
    return out


def scan(item_id, w, h, d, *, label, round_footprint=False, **over) -> dict:
    return {
        "id": item_id, "suitcaseId": "fixture-bag", "label": label, "labelSource": "user",
        "description": f"fixture {label}", "dimensions": [w, h, d], "cellSize": 0.01,
        "heights": grid(w, h, d, round_footprint=round_footprint),
        "mass": 1.0, "keepUpright": False, "rigidity": "rigid", "compressibility": 1.0,
    } | over


SUITCASE = {"_id": "fixture-bag", "name": "Fixture carry-on", "dimensions": [0.55, 0.22, 0.35]}

ITEMS = [
    # soft: physics must hand the solver a squashed height, and the plan must show it
    scan("shirts", 0.30, 0.10, 0.22, label="Stack of shirts", rigidity="soft", compressibility=2.0, mass=0.8),
    scan("hoodie", 0.28, 0.12, 0.20, label="Hoodie", rigidity="soft", compressibility=3.0, mass=0.7),
    # rigid: height must survive untouched
    scan("book", 0.15, 0.04, 0.22, label="Hardback book", mass=1.1),
    scan("charger", 0.10, 0.05, 0.08, label="Charger brick", mass=0.35),
    # fragile
    scan("camera", 0.12, 0.09, 0.10, label="Camera", rigidity="fragile", mass=0.6),
    # upright cylinder: must stay upright AND its rotation string must stay consistent
    scan("bottle", 0.08, 0.20, 0.08, label="Water bottle", round_footprint=True, keepUpright=True, mass=0.6),
    # cylinder that does not fit standing up (0.26 > 0.22 interior height), so the solver
    # must lay it down -- the case where a laid-down cylinder used to arrive tagged "XYZ"
    scan("thermos", 0.09, 0.26, 0.09, label="Thermos", round_footprint=True, mass=0.5),
    # fits in no orientation (0.50 x 0.40 footprint in a 0.55 x 0.35 bag, 0.40/0.50 > 0.22
    # interior height): guarantees the unpacked path is exercised
    scan("guitar", 0.50, 0.12, 0.40, label="Travel guitar", mass=2.4),
]


# ------------------------------------------------- fixture #2 and #3: plans with a real cavity
# `nestedIn` is the one seam nothing used to reach with real solver output. These two fixtures
# reach it (or show exactly what stops it), through `planner.plan` like every other fixture here.
def recess_grid(rows, cols, top, floor, *dips) -> list[list[float]]:
    """A `heights` grid with rectangular dips: `top` everywhere, `floor` on the cells of each
    `(r0, r1, c0, c1)` dip. Rows run along width, columns along depth, as everywhere else.
    Called with four bare numbers for the one-dip case, which is how it started.

    A dip has to line up with `Item.solid_boxes`' max-pooling (4 x 4 blocks, `np.array_split`
    of the row/col indices) or it vanishes: max-pooling takes the TALLEST cell in a block, so a
    block holding one rim cell is solid to its full height. A recess that is not a whole number
    of pooled blocks leaves the item with no cavity at all and no nest is possible -- that is a
    property of the solver's own decomposition, not of the scan.
    """
    if len(dips) == 4 and not isinstance(dips[0], (tuple, list)):
        dips = (dips,)
    return [[floor if any(r0 <= i < r1 and c0 <= j < c1 for r0, r1, c0, c1 in dips) else top
             for j in range(cols)] for i in range(rows)]


# A rigid case with a foam cut-out, and a lens that fits the cut-out. The dip is 1 of the 16
# pooled blocks and 5% of the bounding volume, so `from_scanned_heightmap` still classifies the
# case as a **box** -- which matters: see NEST_GAP_ITEMS.
NEST_SUITCASE = {"_id": "nest-bag", "name": "Fixture camera bag", "dimensions": [0.42, 0.13, 0.26]}
NEST_ITEMS = [
    scan("camera-case", 0.40, 0.10, 0.24, label="Camera case with a foam recess", mass=1.2,
         keepUpright=True, suitcaseId="nest-bag",
         heights=recess_grid(40, 24, 0.10, 0.02, 10, 20, 6, 12)),
    # 0.09 x 0.07 x 0.05 against a 0.10 x 0.06 x 0.08 cut-out: fits inside it, and nowhere else
    # (2 cm of floor beside the case, 3 cm of headroom above it), so a nest is the only way to
    # pack it -- if the solver declines, the item is unpacked and the check says so.
    scan("lens", 0.09, 0.07, 0.05, label="Spare lens", mass=0.4, suitcaseId="nest-bag"),
    # rides on the case's lid: a neighbour that is NOT nested, so the overlap half of the
    # contract still has an ordinary pair to be strict about
    scan("pouch", 0.10, 0.02, 0.08, label="Cable pouch", mass=0.2, suitcaseId="nest-bag"),
]

# The same seam with an open box instead of a recessed one -- the shape a bowl or a shoe really
# scans as. Same construction as tests/plan_contract/make_plan.py's NEST_ITEMS (not imported;
# that file is owned elsewhere). This one does NOT nest today, and check_nested_chain reports
# why rather than passing vacuously.
NEST_GAP_SUITCASE = {"_id": "gap-bag", "name": "Fixture open-box bag", "dimensions": [0.24, 0.10, 0.24]}
NEST_GAP_ITEMS = [
    scan("bowl", 0.22, 0.08, 0.22, label="Open box", mass=0.5, suitcaseId="gap-bag",
         heights=recess_grid(22, 22, 0.08, 0.01, 3, 19, 3, 19)),
    scan("socks", 0.10, 0.04, 0.10, label="Socks", rigidity="soft", compressibility=1.5,
         mass=0.5, suitcaseId="gap-bag"),
]

# ------------------------------------------------- fixture #4: ONE host, TWO cavities, TWO guests
# Every other nesting fixture in this repo has one host, one cavity, one guest -- and the cavity
# cell travels with `nestedIn` precisely because a host can have several, and "may these two
# overlap" has a different answer in each (packing-core/CLAUDE.md, packer3d/decoder._nested_in).
# Nothing exercised that until this fixture.
#
# The geometry is forced, not tuned. `Item.solid_boxes` max-pools `heights` to 4 x 4 blocks
# whatever the grid's resolution, so a cavity cell is always a whole block: here 1/4 of the
# width by 1/4 of the depth, i.e. 0.10 x 0.06 m. Two recesses therefore have to be two of those
# 16 blocks, and the block indices decide everything:
#
#   * two blocks adjacent along an axis read as one recess spanning both, not two cells;
#   * a block at index 0 or 3 touches the item's own wall, so the "case" has an open notch
#     rather than a cut-out with a rim;
#   * index 1 and 2 are the only interior indices per axis, and they are adjacent.
#
# So the only way to get TWO fully-rimmed cavity cells out of a 4 x 4 pool is the diagonal pair
# (1, 1) and (2, 2): each has solid blocks on all four sides, and the two touch only along one
# vertical corner line, which is zero shared volume. That is not a fixture choice, it is what
# the pooling leaves available -- three rimmed cells is not reachable at all.
#
# Depth is forced too: `from_scanned_heightmap` calls a shape `irregular` once true volume / bbox
# drops below 0.9, and irregular defaults to `fragile=True`, which `packer3d/decoder.py` then
# refuses to rest anything on (the KNOWN GAP above). Two of 16 blocks recessed by a fraction f of
# the height gives ratio 1 - f/8, so f <= 0.8 -- and at 0.8 it lands exactly on the boundary.
# f = 0.7 (floor 0.03 m of a 0.10 m case) keeps the ratio at 0.9125 and the case a `box`.
MULTI_SUITCASE = {"_id": "multi-bag", "name": "Fixture two-cavity bag", "dimensions": [0.42, 0.11, 0.26]}
MULTI_ITEMS = [
    scan("camera-case", 0.40, 0.10, 0.24, label="Camera case with two foam cut-outs", mass=1.2,
         keepUpright=True, suitcaseId="multi-bag",
         heights=recess_grid(40, 24, 0.10, 0.03, (10, 20, 6, 12), (20, 30, 12, 18))),
    # Two identical lenses against two identical 0.10 x 0.06 x 0.07 cut-outs. Each fits either
    # cut-out and neither fits anywhere else: 1 cm of floor beside the case, 1 cm of headroom
    # above its lid, and two of them cannot share one cut-out (0.09 m long in a 0.06 m-wide
    # block side by side, 0.05 + 0.05 m tall in 0.07 m of depth plus 0.01 m of headroom). So the
    # solver has exactly one packing left: one lens per cavity, which is the case this fixture
    # exists to reach.
    scan("lens-a", 0.09, 0.06, 0.05, label="Wide lens", mass=0.4, suitcaseId="multi-bag"),
    scan("lens-b", 0.09, 0.06, 0.05, label="Tele lens", mass=0.4, suitcaseId="multi-bag"),
]


# ------------------------------------------------------------------------------- reporting
class Report:
    """Four states, deliberately distinct. A seam that cannot be asserted must never read as
    PASS -- "0 of 7 placements declare nestedIn" printed as PASS is exactly how the nesting
    contract went unexercised for a day: vacuously green, so nobody looked.

      PASS / FAIL  an assertion that ran
      N/A          nothing in this plan to assert against (not a pass, not a failure)
      GAP          the pipeline cannot reach this seam yet, with the diagnosis. Loud, exit 0,
                   and the assertions engage on their own the day the pipeline reaches it.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.gaps: list[str] = []

    def __call__(self, seam: str, ok: bool, detail: str) -> bool:
        print(f"{'PASS' if ok else 'FAIL'}  {seam}\n        {detail}")
        if not ok:
            self.failures.append(seam)
        return ok

    def note(self, seam: str, detail: str) -> None:
        print(f"N/A   {seam}\n        {detail}")

    def gap(self, seam: str, detail: str) -> None:
        print(f"GAP   {seam}\n        {detail}")
        self.gaps.append(seam)


def vec(v: dict) -> tuple[float, float, float]:
    return (float(v["x"]), float(v["y"]), float(v["z"]))


def box(p: dict) -> tuple[tuple, tuple]:
    lo = vec(p["position"])
    size = vec(p["size"])
    return lo, tuple(lo[k] + size[k] for k in range(3))


def overlap(a: dict, b: dict) -> tuple[float, float, float]:
    (alo, ahi), (blo, bhi) = box(a), box(b)
    return tuple(min(ahi[k], bhi[k]) - max(alo[k], blo[k]) for k in range(3))


def fmt(t) -> str:
    return "(" + ", ".join(f"{v:.4f}" for v in t) + ")"


def shared(a: dict, b: dict) -> tuple[tuple, tuple]:
    """The shared volume of two placements' boxes, as (min corner, max corner)."""
    (alo, ahi), (blo, bhi) = box(a), box(b)
    return (tuple(max(alo[k], blo[k]) for k in range(3)),
            tuple(min(ahi[k], bhi[k]) for k in range(3)))


def permitted(a: dict, b: dict) -> bool:
    """`a` is nested in `b` and their whole shared volume lies inside the declared cavity --
    packing-core/CLAUDE.md: "intersection between a nested item and its host is permitted only
    inside `cavity`. Overlap anywhere outside it is still an overlap"."""
    n = a.get("nestedIn")
    if not n or n.get("itemId") != b["itemId"] or not n.get("cavity"):
        return False
    clo, chi = box(n["cavity"])
    lo, hi = shared(a, b)
    return all(lo[k] >= clo[k] - EPS and hi[k] <= chi[k] + EPS for k in range(3))


# ------------------------------------------------------------------------------ the seams
def check_container(rep: Report, plan: dict, suitcase: dict = None) -> None:
    """app_plan's bag frame vs the suitcase document, and every item inside [0, dimensions]."""
    suitcase = suitcase or SUITCASE
    dims = vec(plan["container"]["dimensions"])
    rep("container dimensions == suitcase document [width, height, depth]",
        all(abs(dims[k] - suitcase["dimensions"][k]) <= EPS for k in range(3)),
        f"plan {fmt(dims)} vs document {fmt(tuple(suitcase['dimensions']))}")

    worst_min, worst_max = ("", 0.0), ("", 0.0)
    for p in plan["placements"]:
        lo, hi = box(p)
        for k in range(3):
            if lo[k] < worst_min[1]:
                worst_min = (f"{p['itemId']}.{AXIS[k]}", lo[k])
            over = hi[k] - dims[k]
            if over > worst_max[1]:
                worst_max = (f"{p['itemId']}.{AXIS[k]}", over)
    rep("every placement inside the container, [0, dimensions] on each axis",
        worst_min[1] >= -EPS and worst_max[1] <= EPS,
        f"{len(plan['placements'])} placements; most negative min corner {worst_min[1]:+.4f} m "
        f"({worst_min[0] or 'none'}); largest overshoot past a wall {worst_max[1]:+.4f} m "
        f"({worst_max[0] or 'none'})")


def check_nesting(rep: Report, plan: dict) -> None:
    """packing-core/CLAUDE.md "Nested placements": two boxes may intersect only when one
    declares the other as its `nestedIn` host, and then only inside the stated `cavity`."""
    placements = plan["placements"]
    ids = {p["itemId"] for p in placements}
    nested = [p for p in placements if p.get("nestedIn")]
    bad_host = [f"{p['itemId']} -> {p['nestedIn'].get('itemId')!r}" for p in nested
                if p["nestedIn"].get("itemId") not in ids or p["nestedIn"]["itemId"] == p["itemId"]
                or p["nestedIn"].get("cavity") is None]
    seam = "every nestedIn names another placement in this plan and carries its cavity cell"
    if not nested:
        # NOT a pass: there is nothing here to check. The nesting chain is asserted against the
        # fixture that forces a nest (check_nested_chain), not against a plan with no nesting.
        rep.note(seam, f"no nestedIn in this plan ({len(placements)} placements), so this "
                       f"assertion had nothing to run against")
    else:
        rep(seam, not bad_host, f"{len(nested)} of {len(placements)} placements declare nestedIn; "
                                f"dangling/self/cavity-less: {bad_host or 'none'}")

    offenders, allowed = [], 0
    for i, a in enumerate(placements):
        for b in placements[i + 1:]:
            ov = overlap(a, b)
            if min(ov) <= EPS:
                continue
            if permitted(a, b) or permitted(b, a):
                allowed += 1
                continue
            offenders.append(f"{a['itemId']}/{b['itemId']} by {fmt(ov)} m")
    pairs = len(placements) * (len(placements) - 1) // 2
    rep("no two placements' boxes intersect unless nested inside the declared cavity",
        not offenders, f"{pairs} pairs tested, {allowed} permitted by a cavity, "
                       f"undeclared overlaps: {offenders or 'none'}")


def check_rotation_and_size(rep: Report, plan: dict, own: dict, shapes: dict) -> None:
    """`size` must equal the item's OWN extents permuted by `rotation` (AxisRotation.swift:
    read the string left to right as the item-local axes assigned to bag X, Y, Z).

    This is the check that caught laid-down cylinders arriving tagged "XYZ": the size said
    the thing was lying down and the rotation said it was upright.
    """
    bad, lines = [], []
    for p in plan["placements"]:
        r, size, o = p["rotation"], vec(p["size"]), own[p["itemId"]]
        if len(r) != 3 or set(r) != set("XYZ"):
            bad.append(f"{p['itemId']}: rotation {r!r} is not a permutation of XYZ")
            continue
        want = tuple(o[LETTER[c]] for c in r)
        if any(abs(want[k] - size[k]) > 1e-4 for k in range(3)):
            bad.append(f"{p['itemId']}: rotation {r} over own dims {fmt(o)} implies size "
                       f"{fmt(want)}, plan says {fmt(size)}")
        lines.append(f"{p['itemId']}[{shapes.get(p['itemId'], '?')}] {r} own {fmt(o)} -> size {fmt(size)}")
    rep("size == the item's own dimensions permuted by rotation", not bad,
        "; ".join(lines) + ("\n        MISMATCH: " + "; ".join(bad) if bad else ""))


def check_compression(rep: Report, plan: dict) -> None:
    """A soft item is packed shorter than it was scanned, by exactly the physics allowance;
    a rigid one is packed at its scanned height."""
    # read the packed height out of the PLAN: `rotation` says which bag axis carries the
    # item's own Y (its scanned height), and `size` is the extent along that axis
    by_id = {p["itemId"]: p for p in plan["placements"]}
    soft, rigid, bad = [], [], []
    for doc in ITEMS:
        p = by_id.get(doc["id"])
        if p is None:
            continue
        obj = physics_object(doc)
        scanned = obj.dimensions[1]
        expected = scanned - compression_allowance_m(obj, scanned)
        if "Y" not in p["rotation"]:  # check_rotation_and_size reports the malformed string
            bad.append(f"{doc['id']}: rotation {p['rotation']!r} carries no item Y axis")
            continue
        packed = vec(p["size"])[p["rotation"].index("Y")]
        line = (f"{doc['id']} ({doc['rigidity']}, k={doc.get('compressibility')}): "
                f"scanned {scanned:.4f} -> packed {packed:.4f} m")
        if abs(packed - expected) > 1e-4:
            bad.append(line + f", expected {expected:.4f}")
        elif doc["rigidity"] == "soft":
            soft.append(line)
            if packed >= scanned - EPS:
                bad.append(line + " (soft item was not compressed at all)")
        else:
            rigid.append(line)
            if abs(packed - scanned) > 1e-4:
                bad.append(line + " (rigid item's height changed)")
    rep("soft items packed at the compressed height, rigid items at the scanned height",
        not bad, f"soft: {'; '.join(soft) or 'none'} | rigid: {'; '.join(rigid) or 'none'}"
                 + ("\n        MISMATCH: " + "; ".join(bad) if bad else ""))


def check_upright(rep: Report, plan: dict) -> None:
    """keepUpright survives the orientation remap: bag Y must still be the item's own Y."""
    want = {d["id"] for d in ITEMS if d.get("keepUpright")}
    rows = [(p["itemId"], p["rotation"]) for p in plan["placements"] if p["itemId"] in want]
    tipped = [f"{i}: rotation {r} puts item {r[1]} along bag Y" for i, r in rows if r[1] != "Y"]
    rep("every keepUpright item is still upright", not tipped,
        f"{len(rows)} of {len(want)} keepUpright items are in the plan: "
        f"{', '.join(f'{i}={r}' for i, r in rows) or 'none'}"
        + ("\n        TIPPED: " + "; ".join(tipped) if tipped else ""))


def check_steps(rep: Report, plan: dict) -> None:
    steps = sorted(p["step"] for p in plan["placements"])
    rep("steps form 1...n", steps == list(range(1, len(steps) + 1)),
        f"n={len(steps)}, steps {steps}")


def check_unpacked(rep: Report, doc: dict, plan: dict) -> None:
    """What the plan does with items the solver could not fit: it drops them from
    `plan.placements` entirely and the server document lists them top-level in `unpacked`
    (the iOS PackingPlan contract has no slot for them). Assert the accounting closes."""
    placed = [p["itemId"] for p in plan["placements"]]
    unpacked = [u["itemId"] for u in doc["unpacked"]]
    reasons = {u["id"]: u.get("reason", "") for u in doc["solver"]["unpacked"]}
    every = {d["id"] for d in ITEMS}
    rep("every scanned item is either placed exactly once or listed in the document's "
        "`unpacked` (never in `plan.placements`)",
        len(set(placed)) == len(placed) and set(placed).isdisjoint(unpacked)
        and set(placed) | set(unpacked) == every,
        f"{len(every)} scanned = {len(placed)} placed + {len(unpacked)} unpacked; "
        f"left out: {', '.join(f'{i} ({reasons.get(i, '?')})' for i in unpacked) or 'none'}; "
        f"plan.placements holds no unpacked item: {set(placed).isdisjoint(unpacked)}")


def check_decomposition(rep: Report, doc: dict, plan: dict, items: list = None) -> None:
    """The physics layer decomposes a scanned item's placement into cavity cells
    (`packer3d_adapter._objects_from_placement`, one `Object` per max-pooled `heights` cell).
    Whatever it decomposes into must stay INSIDE the box the solver actually reserved and the
    plan actually shows -- a cell sticking out of it means the layout physics graded is not the
    layout the app will draw, which is the decomposition-mismatch bug class.

    Compared in the physics frame, so the app-frame plan box has to be mapped there:
    app (x, y, z) -> physics (x, y, -z), i.e. physics Z runs [-(z + depth), -z].
    """
    scene, _ = scene_from_packer3d(doc["solver"], items=prepare_items(items or ITEMS))
    reserved = {}
    for p in plan["placements"]:
        lo, size = vec(p["position"]), vec(p["size"])
        reserved[p["itemId"]] = ((lo[0], lo[1], -(lo[2] + size[2])),
                                 (lo[0] + size[0], lo[1] + size[1], -lo[2]))
    escapes, cells = [], 0
    for obj in scene.objects:
        if obj.id.startswith("obstacle:"):
            continue
        cells += 1
        base = obj.id.split("#")[0]
        want = reserved.get(base)
        if want is None:
            escapes.append(f"{obj.id}: no placement named {base} in the plan")
            continue
        lo = tuple(obj.position[k] - obj.dimensions[k] / 2.0 for k in range(3))
        hi = tuple(obj.position[k] + obj.dimensions[k] / 2.0 for k in range(3))
        out = tuple(max(want[0][k] - lo[k], hi[k] - want[1][k], 0.0) for k in range(3))
        if max(out) > 1e-4:
            escapes.append(f"{obj.id} sticks {fmt(out)} m out of {base}'s box "
                           f"{fmt(want[0])}..{fmt(want[1])} (cell {fmt(lo)}..{fmt(hi)})")
    rep("the physics scene's decomposition of each placement stays inside the plan's box",
        not escapes, f"{cells} physics objects for {len(plan['placements'])} placements; "
                     f"outside their own box: {len(escapes)}"
                     + ("\n        " + "\n        ".join(escapes[:6]) if escapes else ""))


def nested_support(doc: dict, ref: dict) -> dict:
    """The three z values that must coincide under a nested guest, in the packer frame.

    Keyed by guest id, for every placement the SOLVER recorded as nested:

      base    the guest's own min z -- where it actually sits
      floor   the floor of the cavity its `nested_in` DECLARES
      plinth  the top of the tallest host SOLID under the guest's footprint, read out of
              `oriented_solid_boxes(host_item, ...)` -- the same max-pooled decomposition the
              solver placed against, which knows nothing about `nested_in`

    `plinth` is the independent one: it comes from the host item's geometry, not from the
    declaration, so it is the only one of the three that can disagree with the other two.
    Empty when the solver nested nothing, so a plan with no nesting excuses nothing.
    """
    nested = [p for p in doc["solver"]["placements"] if p.get("nested_in")]
    solver = {p["item_id"]: p for p in doc["solver"]["placements"]}
    out = {}
    for p in nested:
        host = solver.get(p["nested_in"]["item_id"])
        if host is None or host["item_id"] not in ref:
            continue    # a dangling host is check_nested_chain's failure to report, not this one
        lo = tuple(p["position"])
        hi = tuple(lo[k] + p["dims"][k] for k in range(3))
        solids = oriented_solid_boxes(ref[host["item_id"]], tuple(host["position"]),
                                      host["dims"], host["orientation"])
        under = [bhi[2] for blo, bhi in solids
                 if min(hi[0], bhi[0]) - max(lo[0], blo[0]) > EPS
                 and min(hi[1], bhi[1]) - max(lo[1], blo[1]) > EPS]
        out[p["item_id"]] = {"host": host["item_id"], "base": lo[2],
                             "floor": float(p["nested_in"]["position"][2]),
                             "plinth": max(under) if under else None,
                             "under": len(under), "solids": len(solids)}
    return out


def check_physics_agrees(rep: Report, doc: dict, plan: dict, suitcase: dict = None,
                         items: list = None, support: dict = None) -> None:
    """The verdict stored in the server document is about the packer-frame layout. Rebuild
    the SAME layout out of the app-frame plan JSON (physics_point on the app frame: app
    (x, y, z) -> physics (x, y, -z)) and re-run physics.validator on it. A frame or
    decomposition slip between the two stages shows up as a different verdict."""
    suitcase, items = suitcase or SUITCASE, items or ITEMS
    w, h, d = (float(v) for v in suitcase["dimensions"])
    solver = {p["item_id"]: p for p in doc["solver"]["placements"]}
    upright = {i["id"]: bool(i.get("keep_upright")) for i in prepare_items(items)}
    objects = []
    for p in plan["placements"]:
        lo, size = vec(p["position"]), vec(p["size"])
        sp = solver[p["itemId"]]
        fragile = bool(sp.get("fragile", False))
        objects.append(Object(
            id=p["itemId"], dimensions=size,
            position=(lo[0] + size[0] / 2.0, lo[1] + size[1] / 2.0, -(lo[2] + size[2] / 2.0)),
            mass_kg=float(sp.get("mass", 0.0)),
            constraints=Constraints(fragile=fragile, cannot_support_weight=fragile,
                                    keep_upright=upright.get(p["itemId"], False))))
    rerun = validate_layout(Scene(container=Container(id=plan["container"]["id"], dimensions=(w, h, d),
                                                      position=(w / 2.0, h / 2.0, -d / 2.0)),
                                  objects=objects))
    stored = doc["validation"]

    # A nested placement is a collision AND a floating item to this re-run, and not to the stored
    # verdict, and both are right: the stored one graded the host decomposed into cavity cells,
    # the re-run grades each placement as one solid box, so the cavity's floor is invisible to it.
    # packing-core/CLAUDE.md excuses exactly those two, and only for a declared cavity that really
    # contains the shared volume ("the host's cavity counts as support for the nested item, so it
    # does not read as floating"). The cavity itself is asserted by check_nested_chain, so nothing
    # is waved through on the field's word alone; every other violation must still match.
    #
    # The float half is excused on the GEOMETRY, never on the declaration: `support` (nested_support)
    # says whether the guest's base is in resting contact with the tallest host solid under its
    # footprint, out of the host's own decomposition. This used to be `|guest.y - cavity.y| <= EPS`,
    # which cannot fail -- the producer sets the cavity floor to the guest's base by construction --
    # so it excused every nested float, including one hanging in mid-air. See check_nested_support.
    # No `support` passed means no excuse: strict, which is what a plan with no real nest wants.
    by_id = {p["itemId"]: p for p in plan["placements"]}
    excused_pairs, excused_float = set(), set()
    for a in plan["placements"]:
        b = by_id.get((a.get("nestedIn") or {}).get("itemId", ""))
        if b is None or not permitted(a, b):
            continue
        excused_pairs.add(tuple(sorted((a["itemId"], b["itemId"]))))
        s = (support or {}).get(a["itemId"])
        if s and s["plinth"] is not None and abs(s["base"] - s["plinth"]) <= CONTACT:
            excused_float.add((a["itemId"],))   # really resting on a host solid, not floating in it

    def kinds(v: dict) -> set:
        # the stored verdict decomposes a scanned item into cavity cells (`id#k`); compare
        # the item, not the cell. Collisions name `objects` (a pair), everything else `object`.
        out = set()
        for e in v["violations"]:
            names = e.get("objects") or [e.get("object")]
            key = tuple(sorted(str(n or "?").split("#")[0] for n in names))
            if "COLLISION" in e["type"].upper() and key in excused_pairs:
                continue
            if "UNSUPPORTED" in e["type"].upper() and key in excused_float:
                continue
            out.add((e["type"], key))
        return out

    excused = sorted(excused_pairs | excused_float)
    same = kinds(stored) == kinds(rerun)
    # validity may differ only when every re-run violation is one the cavity explains
    rep("physics verdict in the server document == physics.validator re-run on the plan JSON",
        same and (bool(stored["valid"]) == bool(rerun["valid"]) or (bool(excused) and not kinds(rerun))),
        f"stored valid={stored['valid']} score={stored['score']:.3f} violations={sorted(kinds(stored))} "
        f"| re-run valid={rerun['valid']} score={rerun['score']:.3f} violations={sorted(kinds(rerun))} "
        f"over {len(objects)} placements"
        + (f"; excused by a declared cavity (collision with the host) and by resting contact with "
           f"the host solid under it (the float): {excused}, leaving {sorted(kinds(rerun))} to match"
           if excused else ""))


def plan3d_binary() -> str | None:
    return os.environ.get("PLAN3D_BIN") or next(
        (str(p) for p in sorted(ROOT.glob("tools/plan3d/.build/*/plan3d")) if p.is_file()), None)


def plan3d_run(binary: str, doc: dict) -> tuple[int, str, str]:
    out_dir = tempfile.mkdtemp(prefix="pipeline_check_plan3d_")
    plan_path = Path(out_dir) / "server_document.json"
    plan_path.write_text(json.dumps(doc))
    proc = subprocess.run([binary, str(plan_path), out_dir, "--steps", "--unpacked", "--violations"],
                          capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    said = next((l for l in tail if l.startswith("plan:")), tail[0] if tail else "")
    return proc.returncode, proc.stdout, said


def check_swift_decoder(rep: Report, doc: dict, label: str = "") -> None:
    """The real Swift decoder: PackingPlan.PlanLoader (structural: units, unique item ids,
    contiguous steps) plus PackingPlan.geometryIssues()."""
    seam = f"Swift decoder (tools/plan3d) loads the plan{label} and reports no geometry issues"
    binary = plan3d_binary()
    if not binary:
        rep(seam, False, "no plan3d binary: build it with `swift build --package-path tools/plan3d` "
                         "(see this file's docstring) or set PLAN3D_BIN")
        return
    code, out, said = plan3d_run(binary, doc)
    rep(seam, code == 0 and "geometry issues: none" in out,
        f"{Path(binary).name} exit={code}: {said or '(no output)'}"
        + ("" if code == 0 else f"\n        stdout/stderr: {out.strip()[:400]}"))


def check_nested_chain(rep: Report, doc: dict, plan: dict, ref: dict, label: str) -> None:
    """The `nestedIn` chain, end to end, on a plan the REAL solver produced -- the one seam that
    used to be reachable only through a fabricated `--perturb nest` plan.

    packer3d's `nested_in` -> app_plan's `nestedIn` (a y/z frame swap) -> the shared volume of
    the pair -> the Swift decoder -> the physics verdict. If the solver did not nest, this reports
    a GAP with the diagnosis instead of a vacuous PASS, and every assertion below engages on its
    own the day it does.
    """
    solver = {p["item_id"]: p for p in doc["solver"]["placements"]}
    nested = [p for p in doc["solver"]["placements"] if p.get("nested_in")]
    if not nested:
        # Why not? A host needs a cavity in its own solid decomposition (occupied < bbox) AND it
        # must be able to bear weight: the decoder rejects any candidate resting on a fragile
        # solid, and the cavity floor is one of the host's solids.
        hosts = [(it, it.occupied_volume < it.bbox_volume - EPS) for it in ref.values()]
        lines = [f"{it.id}[{getattr(it, 'scan_shape', '?')}] cavity={'yes' if cav else 'no'} "
                 f"(occupied {it.occupied_volume:.6f} of bbox {it.bbox_volume:.6f} m3) "
                 f"fragile={it.fragile}" for it, cav in hosts]
        blocked = [it.id for it, cav in hosts if cav and it.fragile]
        left = [f"{u['id']} ({u.get('reason', '?')})" for u in doc["solver"]["unpacked"]]
        rep.gap(f"packer3d emits nested_in on {label}",
                f"no placement declares nested_in. unpacked: {', '.join(left) or 'none'}\n        "
                + "; ".join(lines)
                + (f"\n        {', '.join(blocked)} has a real cavity but is fragile, and "
                   f"packer3d/decoder.py's fragile-below rule rejects any candidate whose base "
                   f"rests on a fragile solid -- the cavity floor is one. A scanned shape with a "
                   f"genuine cavity classifies as `irregular` (true volume / bbox < 0.9), and "
                   f"`Item.from_scanned_heightmap` defaults irregular to fragile=True, which "
                   f"`physics/prepack.packable` only ever sets and never clears. So this is not "
                   f"the solver declining a cavity it could use: the cavity is unreachable by "
                   f"construction for every open-topped scan." if blocked else ""))
        return

    # ---- 1. the solver's own record: right host, cavity inside it, clear of its solids.
    # The host's solids come from packer3d's own `oriented_solid_boxes`, so this is the cavity
    # against the decomposition the solver actually placed against, not a second copy of it.
    bad, lines = [], []
    for p in nested:
        n = p["nested_in"]
        host = solver.get(n.get("item_id"))
        if host is None or p["item_id"] == n.get("item_id"):
            bad.append(f"{p['item_id']}: nested_in names {n.get('item_id')!r}, not another placement here")
            continue
        clo, chi = tuple(n["position"]), tuple(n["position"][k] + n["dims"][k] for k in range(3))
        hlo = tuple(host["position"])
        hhi = tuple(hlo[k] + host["dims"][k] for k in range(3))
        if any(clo[k] < hlo[k] - EPS or chi[k] > hhi[k] + EPS for k in range(3)):
            bad.append(f"{p['item_id']}: cavity {fmt(clo)}..{fmt(chi)} is not inside "
                       f"{host['item_id']} {fmt(hlo)}..{fmt(hhi)}")
        solids = oriented_solid_boxes(ref[host["item_id"]], hlo, host["dims"], host["orientation"])
        hit = [(blo, bhi) for blo, bhi in solids
               if all(min(chi[k], bhi[k]) - max(clo[k], blo[k]) > EPS for k in range(3))]
        if hit:
            bad.append(f"{p['item_id']}: cavity {fmt(clo)}..{fmt(chi)} cuts into {len(hit)} of "
                       f"{host['item_id']}'s {len(solids)} solid sub-boxes, first "
                       f"{fmt(hit[0][0])}..{fmt(hit[0][1])}")
        lines.append(f"{p['item_id']} in {host['item_id']}: cavity {fmt(clo)}..{fmt(chi)} "
                     f"(packer frame) inside host {fmt(hlo)}..{fmt(hhi)}, clear of all "
                     f"{len(solids)} host solids")
    rep(f"packer3d's nested_in on {label}: cavity inside the host and clear of its solids",
        not bad, "; ".join(lines) + ("\n        MISMATCH: " + "; ".join(bad) if bad else ""))

    # ---- 2. app_plan carried it across with the axes swapped. Packer frame is x = length,
    # y = depth, z = up; the bag frame is x = width, y = up, z = depth -- so app = (x, z, y) for
    # both the min corner and the extent. Frame conversions have been wrong twice today, so the
    # swap is spelled out here rather than trusted.
    by_id = {p["itemId"]: p for p in plan["placements"]}
    bad, lines = [], []
    for p in nested:
        n, app = p["nested_in"], by_id.get(p["item_id"], {}).get("nestedIn")
        if not app:
            bad.append(f"{p['item_id']}: solver said nested_in, plan says nestedIn={app!r} -- dropped")
            continue
        want_pos = (n["position"][0], n["position"][2], n["position"][1])
        want_size = (n["dims"][0], n["dims"][2], n["dims"][1])
        got_pos, got_size = vec(app["cavity"]["position"]), vec(app["cavity"]["size"])
        if app["itemId"] != n["item_id"]:
            bad.append(f"{p['item_id']}: host {n['item_id']} became {app['itemId']}")
        if any(abs(want_pos[k] - got_pos[k]) > 1e-9 for k in range(3)) \
                or any(abs(want_size[k] - got_size[k]) > 1e-9 for k in range(3)):
            bad.append(f"{p['item_id']}: packer cavity {fmt(tuple(n['position']))} ext "
                       f"{fmt(tuple(n['dims']))} swaps to {fmt(want_pos)} ext {fmt(want_size)}, "
                       f"plan says {fmt(got_pos)} ext {fmt(got_size)}")
        # the placement itself must have taken the same swap, or the cavity is in a frame of its own
        sp = by_id[p["item_id"]]
        if any(abs((p["position"][0], p["position"][2], p["position"][1])[k] - vec(sp["position"])[k]) > 1e-9
               for k in range(3)):
            bad.append(f"{p['item_id']}: placement position {tuple(p['position'])} did not take the "
                       f"same y/z swap as its cavity (plan says {fmt(vec(sp['position']))})")
        lines.append(f"{p['item_id']} in {app['itemId']}: cavity {fmt(got_pos)} ext {fmt(got_size)} "
                     f"(bag frame) from packer {fmt(tuple(n['position']))} ext {fmt(tuple(n['dims']))}")
    rep(f"app_plan copied nested_in into nestedIn on {label} with packer (x, y, z) -> bag (x, z, y)",
        not bad, "; ".join(lines) + ("\n        MISMATCH: " + "; ".join(bad) if bad else ""))

    # ---- 3. the pair's shared volume lies inside the declared cavity (the consumer's own rule)
    bad, lines = [], []
    for p in nested:
        a = by_id.get(p["item_id"])
        b = by_id.get(p["nested_in"]["item_id"])
        if not a or not b or not a.get("nestedIn"):
            continue
        lo, hi = shared(a, b)
        clo, chi = box(a["nestedIn"]["cavity"])
        out = tuple(max(clo[k] - lo[k], hi[k] - chi[k], 0.0) for k in range(3))
        line = (f"{a['itemId']}/{b['itemId']} share {fmt(lo)}..{fmt(hi)} "
                f"({(hi[0]-lo[0])*(hi[1]-lo[1])*(hi[2]-lo[2])*1e6:.1f} cm3) inside cavity "
                f"{fmt(clo)}..{fmt(chi)}")
        (lines if max(out) <= EPS else bad).append(line + (f", out by {fmt(out)} m" if max(out) > EPS else ""))
    rep(f"the nested pair's shared volume lies inside the declared cavity on {label}",
        not bad, "; ".join(lines) + ("\n        OUTSIDE: " + "; ".join(bad) if bad else ""))

    # ---- 4. the Swift decoder: silent on the nested pair, still loud on a genuine overlap.
    # The negative is the same plan with `nestedIn` removed -- identical geometry, no declaration,
    # so a decoder that suppresses overlaps by proximity rather than by the field would stay quiet.
    binary = plan3d_binary()
    seam = f"Swift decoder is silent on the nested pair but reports the same overlap undeclared ({label})"
    if not binary:
        rep(seam, False, "no plan3d binary: set PLAN3D_BIN (see this file's docstring)")
    else:
        good_code, good_out, good_said = plan3d_run(binary, doc)
        stripped = json.loads(json.dumps(doc))
        for p in stripped["plan"]["placements"]:
            p["nestedIn"] = None
        bad_code, bad_out, bad_said = plan3d_run(binary, stripped)
        quiet = good_code == 0 and "geometry issues: none" in good_out
        loud = "geometry issues: none" not in bad_out
        rep(seam, quiet and loud,
            f"as planned: exit={good_code} {good_said or '(no output)'}\n        "
            f"with nestedIn stripped: exit={bad_code} "
            f"{next((l for l in bad_out.splitlines() if 'geometry issue' in l or 'overlap' in l), bad_said) or '(no output)'}")

        # ---- 5. the two other ways a `nestedIn` can be wrong, on the same real plan. Both are
        # in PackingPlan.honouredNesting()'s contract and neither was exercised against solver
        # output: a host the plan does not contain is its OWN issue (not silently ignored), and a
        # host chain that loops is treated as not-nested, so the overlap it claimed to explain
        # comes back. Same geometry both times -- only the declaration changes.
        one = nested[0]
        host_id = one["nested_in"]["item_id"]
        dangling = json.loads(json.dumps(doc))
        for p in dangling["plan"]["placements"]:
            if p["itemId"] == one["item_id"]:
                p["nestedIn"]["itemId"] = "no-such-item"
        d_code, d_out, d_said = plan3d_run(binary, dangling)
        cyclic = json.loads(json.dumps(doc))
        cav = next(p["nestedIn"]["cavity"] for p in cyclic["plan"]["placements"]
                   if p["itemId"] == one["item_id"])
        for p in cyclic["plan"]["placements"]:
            if p["itemId"] == host_id:
                p["nestedIn"] = {"itemId": one["item_id"], "cavity": cav}
        c_code, c_out, c_said = plan3d_run(binary, cyclic)
        want = f"{one['item_id']} overlaps {host_id}"
        rep(f"a dangling nestedIn host raises its own issue and a host cycle is not-nested ({label})",
            "which the plan does not contain" in d_out
            and (want in c_out or f"{host_id} overlaps {one['item_id']}" in c_out),
            f"host retagged 'no-such-item': exit={d_code} "
            f"{next((l for l in d_out.splitlines() if 'geometry issue' in l), d_said) or '(no output)'}"
            f"\n        {host_id} also declared nested in {one['item_id']} (a 2-cycle): exit={c_code} "
            f"{next((l for l in c_out.splitlines() if 'geometry issue' in l), c_said) or '(no output)'}")


def check_nested_support(rep: Report, doc: dict, plan: dict, ref: dict, label: str) -> None:
    """A nested guest's SUPPORT, asserted as support rather than as geometry.

    `packer3d/decoder._nested_in` builds the cavity's floor by raising `c0[2]` to the top of the
    tallest host solid under the guest's footprint, and the Swift side turns that floor into a
    plinth which suppresses `.floating` for the guest. Every other assertion in this file is
    satisfied by the DECLARATION alone: the cavity is inside the host, clear of its solids, and
    contains the whole shared volume -- all still true of a guest hanging in mid-air inside a
    correctly-shaped cavity. And `|guest.y - cavity.y| <= EPS`, which check_physics_agrees used to
    excuse the guest's float with, cannot fail: the producer sets `c0 = max(guest.lo, host.lo)` and
    only ever raises z to a solid the candidate was already proved clear of, so the declared floor
    IS the guest's base by construction. It restated the declaration; it tested nothing.

    So the plinth is checked here against the host's own max-pooled decomposition
    (`oriented_solid_boxes` -- the very boxes the solver placed against, which know nothing about
    `nested_in`): the guest's base must be in resting contact with the tallest host solid under its
    footprint AND with the declared floor. A plinth one pooled block too low, or a cavity declaring
    a floor the host's solids do not reach, separates the two -- and the negative control below is
    exactly that plan, hand-edited the way `--perturb` does, because the real solver cannot produce
    it: the guest that floats inside a correctly-declared cavity.
    """
    sup = nested_support(doc, ref)
    if not sup:
        rep.note(f"a nested guest rests on the host solid under it ({label})",
                 "the solver recorded no nested placement here, so this assertion had nothing to "
                 "run against (check_nested_chain reports why)")
        return
    bad, lines = [], []
    for gid, s in sorted(sup.items()):
        if s["plinth"] is None:
            bad.append(f"{gid}: no host solid under its footprint at all -- {s['host']} cannot be "
                       f"holding it up, whatever the cavity declares")
            continue
        air, sunk = s["base"] - s["plinth"], s["floor"] - s["base"]
        if abs(air) > CONTACT:
            bad.append(f"{gid}: base z={s['base']:.4f} is {air:+.4f} m off the tallest of "
                       f"{s['host']}'s {s['under']} solids under its footprint (top "
                       f"z={s['plinth']:.4f}) -- {'air beneath it' if air > 0 else 'sunk into it'}")
        if abs(sunk) > CONTACT:
            bad.append(f"{gid}: declared cavity floor z={s['floor']:.4f} is {sunk:+.4f} m off the "
                       f"guest's base z={s['base']:.4f}")
        lines.append(f"{gid} on {s['host']}: base z={s['base']:.4f}, declared floor "
                     f"z={s['floor']:.4f}, tallest of {s['under']} host solids under its footprint "
                     f"(of {s['solids']}) tops at z={s['plinth']:.4f}")
    rep(f"a nested guest's base is in resting contact with the host solid under it AND with the "
        f"declared cavity floor, within {CONTACT * 1000:.0f} mm ({label})",
        not bad, "; ".join(lines) + ("\n        UNSUPPORTED: " + "; ".join(bad) if bad else ""))

    # ---- the negative control, the plan that separates support from geometry: lift one guest 8 mm
    # inside its own cavity and lift the declared floor with it, so the DECLARATION stays perfectly
    # self-consistent -- the guest still sits exactly on the floor it claims, the cavity is still
    # inside the host, still clear of its solids, still contains the whole shared volume -- and only
    # the host's real solids say the guest is in the air. Nothing else in the pipeline sees it.
    lift = 0.008
    gid = sorted(sup)[0]
    broken = json.loads(json.dumps(doc))
    for sp in broken["solver"]["placements"]:
        if sp["item_id"] == gid:
            sp["position"][2] += lift
            sp["nested_in"]["position"][2] += lift
            sp["nested_in"]["dims"][2] -= lift
    for bp in broken["plan"]["placements"]:
        if bp["itemId"] == gid:               # bag frame: y is up, packer z
            bp["position"]["y"] = float(bp["position"]["y"]) + lift
            bp["nestedIn"]["cavity"]["position"]["y"] = float(bp["nestedIn"]["cavity"]["position"]["y"]) + lift
            bp["nestedIn"]["cavity"]["size"]["y"] = float(bp["nestedIn"]["cavity"]["size"]["y"]) - lift
    b = nested_support(broken, ref)[gid]
    caught = b["plinth"] is not None and abs(b["base"] - b["plinth"]) > CONTACT
    by_id = {q["itemId"]: q for q in broken["plan"]["placements"]}
    still_permitted = permitted(by_id[gid], by_id[b["host"]])
    old_excuse = abs(vec(by_id[gid]["position"])[1]
                     - vec(by_id[gid]["nestedIn"]["cavity"]["position"])[1])
    binary = plan3d_binary()
    seam = (f"a guest floating inside a correctly-declared cavity is caught by the plinth rule, and "
            f"by nothing else ({label})")
    if not binary:
        rep(seam, False, "no plan3d binary: set PLAN3D_BIN (see this file's docstring)")
        return
    code, out, said = plan3d_run(binary, broken)
    rep(seam, caught and still_permitted and code == 0 and "geometry issues: none" in out,
        f"{gid} lifted {lift * 1000:.0f} mm with its declared floor: base z={b['base']:.4f}, "
        f"declared floor z={b['floor']:.4f} (still equal, so the old "
        f"|guest.y - cavity.y| excuse reads {old_excuse:.4f} m and would still excuse it), "
        f"{b['host']}'s solids under it top at z={b['plinth']:.4f}\n        "
        f"plinth rule: {'CAUGHT, air beneath it' if caught else 'SILENT -- the plinth is not checked'}"
        f" | pair rule: still permitted={still_permitted}"
        f" | Swift decoder: exit={code} {said or '(no output)'}")


def check_multi_cavity(rep: Report, doc: dict, plan: dict, label: str) -> None:
    """The seam only a host with SEVERAL cavities can reach: the cavity cell in `nestedIn` has to
    be the guest's OWN cell, not "somewhere in this host".

    Every other nesting fixture has one host, one cavity, one guest, and under that shape a
    consumer that read `nestedIn` as "these two may overlap anywhere inside the host" would pass
    every assertion in this file. packing-core/CLAUDE.md says the cell travels with the field
    exactly because that reading is wrong once a host has two cells. Three assertions, in
    increasing strength:

      1. two guests, one host, two DIFFERENT cells;
      2. each guest's overlap with the host is inside its OWN cell -- and, for at least one of
         them, NOT inside the other guest's cell, so the pass is not the union of the two cells
         quietly standing in for either;
      3. put each guest in the other's cell and leave the declarations alone: the same two boxes,
         the same total overlap volume, the same host. The Python pair rule and the Swift decoder
         must BOTH report it. Under the union reading, or with one cavity per host, this plan is
         indistinguishable from the real one -- that difference is the whole reason for the cell.
    """
    by_id = {p["itemId"]: p for p in plan["placements"]}
    guests: dict[str, list[dict]] = {}
    for p in plan["placements"]:
        if p.get("nestedIn") and p["nestedIn"]["itemId"] in by_id:
            guests.setdefault(p["nestedIn"]["itemId"], []).append(p)
    host_id, crowd = max(guests.items(), key=lambda kv: len(kv[1]), default=("", []))
    seam = f"one host carries two guests in two different cavity cells on {label}"
    if len(crowd) < 2:
        left = [f"{u['id']} ({u.get('reason', '?')})" for u in doc["solver"]["unpacked"]]
        rep.gap(seam, f"the solver did not put two guests in one host: nested pairs "
                      f"{[(p['itemId'], p['nestedIn']['itemId']) for p in plan['placements'] if p.get('nestedIn')]}, "
                      f"unpacked {', '.join(left) or 'none'}. Every assertion below engages on "
                      f"its own the day it does.")
        return

    host = by_id[host_id]
    cells = {p["itemId"]: box(p["nestedIn"]["cavity"]) for p in crowd}
    pairs = [(a["itemId"], b["itemId"]) for i, a in enumerate(crowd) for b in crowd[i + 1:]]
    same = [f"{x}/{y}" for x, y in pairs
            if min(min(cells[y][1][k] - cells[x][0][k], cells[x][1][k] - cells[y][0][k])
                   for k in range(3)) > EPS]
    rep(seam, not same,
        f"{len(crowd)} guests in {host_id}: "
        + "; ".join(f"{p['itemId']} cell {fmt(cells[p['itemId']][0])}..{fmt(cells[p['itemId']][1])}"
                    for p in crowd)
        + (f"\n        SHARE VOLUME: {', '.join(same)} -- not distinct cells" if same else ""))

    # ---- 2. own cell, and demonstrably not merely the union of the cells
    union = (tuple(min(c[0][k] for c in cells.values()) for k in range(3)),
             tuple(max(c[1][k] for c in cells.values()) for k in range(3)))
    bad, lines, discriminating = [], [], 0
    for p in crowd:
        lo, hi = shared(p, host)
        clo, chi = cells[p["itemId"]]
        out = tuple(max(clo[k] - lo[k], hi[k] - chi[k], 0.0) for k in range(3))
        others = [q["itemId"] for q in crowd if q is not p
                  and all(lo[k] >= cells[q["itemId"]][0][k] - EPS
                          and hi[k] <= cells[q["itemId"]][1][k] + EPS for k in range(3))]
        in_union = all(lo[k] >= union[0][k] - EPS and hi[k] <= union[1][k] + EPS for k in range(3))
        if max(out) > EPS:
            bad.append(f"{p['itemId']}'s overlap with {host_id} is out of its own cell by {fmt(out)} m")
        if others:
            bad.append(f"{p['itemId']}'s overlap also fits inside {', '.join(others)}'s cell")
        else:
            discriminating += 1
        lines.append(f"{p['itemId']}/{host_id} share {fmt(lo)}..{fmt(hi)}: inside its own cell, "
                     f"inside another guest's cell {bool(others)}, inside the union of both {in_union}")
    rep(f"each guest's overlap with the host lies inside its OWN cavity cell, not the union, on {label}",
        not bad and discriminating > 0,
        f"union of the {len(cells)} cells is {fmt(union[0])}..{fmt(union[1])}, which every overlap "
        f"fits inside -- so {discriminating} of {len(crowd)} guests tell the cell apart from the union. "
        + "; ".join(lines) + ("\n        WRONG CELL: " + "; ".join(bad) if bad else ""))

    # ---- 3. swap the guests between cells, leave `nestedIn` alone
    swapped = json.loads(json.dumps(doc))
    sw = {p["itemId"]: p for p in swapped["plan"]["placements"]}
    a, b = crowd[0]["itemId"], crowd[1]["itemId"]
    sw[a]["position"], sw[b]["position"] = dict(sw[b]["position"]), dict(sw[a]["position"])
    caught = [f"{g}/{host_id}" for g in (a, b) if not permitted(sw[g], sw[host_id])]
    seam = (f"a guest moved into the other guest's cell is reported, though the declarations, the "
            f"host and the overlap volume are unchanged ({label})")
    binary = plan3d_binary()
    if not binary:
        rep(seam, False, "no plan3d binary: set PLAN3D_BIN (see this file's docstring)")
        return
    code, out, said = plan3d_run(binary, swapped)
    loud = "geometry issues: none" not in out
    rep(seam, len(caught) == 2 and loud,
        f"{a} and {b} traded positions ({fmt(vec(sw[a]['position']))} and "
        f"{fmt(vec(sw[b]['position']))}), each still declaring the cell it left.\n        "
        f"pair rule reports: {', '.join(caught) or 'NOTHING -- the cell was not checked'}\n        "
        f"Swift decoder: exit={code} "
        f"{next((l for l in out.splitlines() if 'geometry issue' in l), said) or '(no output)'}")


# ------------------------------------------------------------------------------ perturbation
def perturb(kind: str, doc: dict) -> str:
    """Break one thing so an assertion can be seen failing. In memory only."""
    plan = doc["plan"]
    p = plan["placements"][0]
    if kind == "escape":
        wall = float(plan["container"]["dimensions"]["z"])
        p["position"]["z"] = wall - float(p["size"]["z"]) + 0.05  # 5 cm out through the far wall
        return f"shifted {p['itemId']} 5 cm out through the bag's far z wall (z = {wall:.3f} m)"
    if kind == "rotation":
        p["rotation"] = "XYZ" if p["rotation"] != "XYZ" else "YXZ"
        return f"retagged {p['itemId']}'s rotation as {p['rotation']} without touching size"
    if kind == "overlap":
        q = plan["placements"][1]
        q["position"] = dict(p["position"])
        return f"moved {q['itemId']} on top of {p['itemId']} with no nestedIn"
    if kind in ("nest", "nest_outside"):
        # The main fixture has no nesting of its own (NEST_ITEMS is the fixture the real solver
        # nests on). Fabricate one here to see the consumer side of the contract both ways:
        # `nest` declares a cavity that contains the whole intersection (must be PERMITTED),
        # `nest_outside` declares one too short to contain it (must still be REPORTED).
        q = plan["placements"][1]
        q["position"] = dict(p["position"])
        (alo, ahi), (blo, bhi) = box(p), box(q)
        lo = tuple(max(alo[k], blo[k]) for k in range(3))
        hi = tuple(min(ahi[k], bhi[k]) for k in range(3))
        shrink = 0.5 if kind == "nest_outside" else 1.0
        q["nestedIn"] = {"itemId": p["itemId"],
                         "cavity": {"position": {"x": lo[0], "y": lo[1], "z": lo[2]},
                                    "size": {"x": hi[0] - lo[0], "y": (hi[1] - lo[1]) * shrink,
                                             "z": hi[2] - lo[2]}}}
        return (f"moved {q['itemId']} into {p['itemId']} and declared nestedIn with a cavity "
                f"{'covering' if shrink == 1.0 else 'covering only half of'} the intersection")
    if kind == "step":
        p["step"] = len(plan["placements"]) + 1
        return f"renumbered {p['itemId']}'s step to {p['step']}"
    if kind == "height":
        soft = next(q for q in plan["placements"]
                    if next(d for d in ITEMS if d["id"] == q["itemId"])["rigidity"] == "soft")
        soft["size"]["y"] = float(soft["size"]["y"]) * 2.0
        return f"un-compressed {soft['itemId']}: doubled its bag-Y size"
    raise SystemExit(f"unknown perturbation {kind!r}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--perturb", choices=("escape", "rotation", "overlap", "nest", "nest_outside", "step", "height"),
                    help="corrupt the plan after planning, to prove an assertion fails")
    args = ap.parse_args()

    # wall-clock budget -> iteration budget, so the optimised candidates are reproducible
    # (`time_budget_s=0` means "iterations only", packer3d/search.py)
    config = planner.OptimizerConfig
    planner.OptimizerConfig = lambda **kw: config(seed=kw.get("seed", 0), time_budget_s=0.0,
                                                  max_iterations=OPTIMIZER_ITERATIONS)
    doc = planner.plan(SUITCASE, ITEMS)
    plan = doc["plan"]

    # the item's OWN extents in bag axes (X = width, Y = height, Z = depth), straight out of
    # the same loader the planner uses, so `rotation` and `size` are checked against what the
    # solver actually held rather than against a second copy of the frame maths
    _, ref, _, _ = load_scenario({"container": {"id": "ref", "dims": [9.0, 9.0, 9.0]},
                                  "items": prepare_items(ITEMS)})
    own = {it.id: swap_yz(it.dims) for it in ref}
    shapes = {it.id: getattr(it, "scan_shape", "?") for it in ref}

    print(f"suitcase {SUITCASE['dimensions']} m (w, h, d), {len(ITEMS)} fixture scans, "
          f"strategy={doc['chosen']['strategy']} seed={doc['chosen']['seed']}, "
          f"{len(plan['placements'])} placed, {len(doc['unpacked'])} unpacked")
    if args.perturb:
        print(f"PERTURBED ({args.perturb}): {perturb(args.perturb, doc)}")
    print()

    rep = Report()
    check_container(rep, plan)
    check_nesting(rep, plan)
    check_rotation_and_size(rep, plan, own, shapes)
    check_compression(rep, plan)
    check_upright(rep, plan)
    check_steps(rep, plan)
    check_unpacked(rep, doc, plan)
    check_decomposition(rep, doc, plan)
    check_physics_agrees(rep, doc, plan, support=nested_support(doc, {it.id: it for it in ref}))
    check_swift_decoder(rep, doc)

    # the cavity seam, on plans the real solver produced. `--perturb` only ever touches the main
    # fixture, so these two run as planned whatever it asked for.
    for name, bag, its in (("the recessed-case fixture", NEST_SUITCASE, NEST_ITEMS),
                           ("the two-cavity fixture", MULTI_SUITCASE, MULTI_ITEMS),
                           ("the open-box fixture", NEST_GAP_SUITCASE, NEST_GAP_ITEMS)):
        d2 = planner.plan(bag, its)
        p2 = d2["plan"]
        _, ref2, _, _ = load_scenario({"container": {"id": "ref", "dims": [9.0, 9.0, 9.0]},
                                       "items": prepare_items(its)})
        ref2 = {it.id: it for it in ref2}
        print(f"\n{name}: suitcase {bag['dimensions']} m (w, h, d), {len(its)} scans, "
              f"strategy={d2['chosen']['strategy']} seed={d2['chosen']['seed']}, "
              f"{len(p2['placements'])} placed, {len(d2['unpacked'])} unpacked")
        check_container(rep, p2, bag)
        check_nesting(rep, p2)   # the pair-strictness seam, now with a real cavity in the plan
        check_steps(rep, p2)
        check_swift_decoder(rep, d2, f" ({name})")
        check_nested_chain(rep, d2, p2, ref2, name)
        if its is MULTI_ITEMS:
            check_multi_cavity(rep, d2, p2, name)
        if any(p.get("nested_in") for p in d2["solver"]["placements"]):
            check_nested_support(rep, d2, p2, ref2, name)
            check_decomposition(rep, d2, p2, its)
            check_physics_agrees(rep, d2, p2, bag, its, support=nested_support(d2, ref2))

    print()
    if rep.failures:
        print(f"FAILED {len(rep.failures)} seam(s): " + "; ".join(rep.failures))
        return 1
    if rep.gaps:
        print(f"all seams hold, {len(rep.gaps)} KNOWN GAP: " + "; ".join(rep.gaps))
        return 0
    print("all seams hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
