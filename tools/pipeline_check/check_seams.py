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
after planning, in memory, so each assertion can be seen catching its own seam.

KNOWN RED, as of 2026-09-12 (this is what the check found on its first run, not a flaw in
the fixture): the last two seams fail because `physics/packer3d_adapter.item_metadata` hands
`_cavity_local_boxes` the *uncompressed* scan `heights`/`dimensions`, while packer3d packed
`Item.compressed(k)`. Every soft scanned item is therefore graded as a stack of cavity cells
taller than the box the solver reserved, which invents collisions and "unsupported" verdicts
for correct plans -- and `server/planner.rank` ranks candidates on that verdict. Scaling the
grid and height by the same k `packer3d/scenario.py::_item_from_dict` uses (or having
`physics/prepack.py` write the squashed height into the document it emits) turns both green;
verified by monkeypatching `item_metadata`. Both files are owned elsewhere, so the fix is not
in this directory.
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
from packer3d.scenario import load_scenario  # noqa: E402
from physics.compressibility import compression_allowance_m  # noqa: E402
from physics.packer3d_adapter import scene_from_packer3d, swap_yz  # noqa: E402
from physics.prepack import physics_object, prepare_items  # noqa: E402
from physics.schema import Constraints, Container, Object, Scene  # noqa: E402
from physics.validator import validate_layout  # noqa: E402

OPTIMIZER_ITERATIONS = 40   # SA iterations per optimised candidate, in place of a wall-clock budget
EPS = 1e-6          # geometry slack: the solver works in metres, plans are printed as floats
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


# ------------------------------------------------------------------------------- reporting
class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def __call__(self, seam: str, ok: bool, detail: str) -> bool:
        print(f"{'PASS' if ok else 'FAIL'}  {seam}\n        {detail}")
        if not ok:
            self.failures.append(seam)
        return ok


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


# ------------------------------------------------------------------------------ the seams
def check_container(rep: Report, plan: dict) -> None:
    """app_plan's bag frame vs the suitcase document, and every item inside [0, dimensions]."""
    dims = vec(plan["container"]["dimensions"])
    rep("container dimensions == suitcase document [width, height, depth]",
        all(abs(dims[k] - SUITCASE["dimensions"][k]) <= EPS for k in range(3)),
        f"plan {fmt(dims)} vs document {fmt(tuple(SUITCASE['dimensions']))}")

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
    rep("every nestedIn names another placement in this plan and carries its cavity cell",
        not bad_host, f"{len(nested)} of {len(placements)} placements declare nestedIn; "
                      f"dangling/self/cavity-less: {bad_host or 'none'}")

    def permitted(a: dict, b: dict) -> bool:
        """The intersection of a nested item with its host, inside the declared cavity."""
        n = a.get("nestedIn")
        if not n or n.get("itemId") != b["itemId"] or not n.get("cavity"):
            return False
        clo, chi = box(n["cavity"])
        (alo, ahi), (blo, bhi) = box(a), box(b)
        for k in range(3):
            lo, hi = max(alo[k], blo[k]), min(ahi[k], bhi[k])
            if lo < clo[k] - EPS or hi > chi[k] + EPS:
                return False
        return True

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


def check_decomposition(rep: Report, doc: dict, plan: dict) -> None:
    """The physics layer decomposes a scanned item's placement into cavity cells
    (`packer3d_adapter._objects_from_placement`, one `Object` per max-pooled `heights` cell).
    Whatever it decomposes into must stay INSIDE the box the solver actually reserved and the
    plan actually shows -- a cell sticking out of it means the layout physics graded is not the
    layout the app will draw, which is the decomposition-mismatch bug class.

    Compared in the physics frame, so the app-frame plan box has to be mapped there:
    app (x, y, z) -> physics (x, y, -z), i.e. physics Z runs [-(z + depth), -z].
    """
    scene, _ = scene_from_packer3d(doc["solver"], items=prepare_items(ITEMS))
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


def check_physics_agrees(rep: Report, doc: dict, plan: dict) -> None:
    """The verdict stored in the server document is about the packer-frame layout. Rebuild
    the SAME layout out of the app-frame plan JSON (physics_point on the app frame: app
    (x, y, z) -> physics (x, y, -z)) and re-run physics.validator on it. A frame or
    decomposition slip between the two stages shows up as a different verdict."""
    w, h, d = (float(v) for v in SUITCASE["dimensions"])
    solver = {p["item_id"]: p for p in doc["solver"]["placements"]}
    upright = {i["id"]: bool(i.get("keep_upright")) for i in prepare_items(ITEMS)}
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

    def kinds(v: dict) -> set:
        # the stored verdict decomposes a scanned item into cavity cells (`id#k`); compare
        # the item, not the cell. Collisions name `objects` (a pair), everything else `object`.
        out = set()
        for e in v["violations"]:
            names = e.get("objects") or [e.get("object")]
            out.add((e["type"], tuple(sorted(str(n or "?").split("#")[0] for n in names))))
        return out

    rep("physics verdict in the server document == physics.validator re-run on the plan JSON",
        bool(stored["valid"]) == bool(rerun["valid"]) and kinds(stored) == kinds(rerun),
        f"stored valid={stored['valid']} score={stored['score']:.3f} violations={sorted(kinds(stored))} "
        f"| re-run valid={rerun['valid']} score={rerun['score']:.3f} violations={sorted(kinds(rerun))} "
        f"over {len(objects)} placements")


def check_swift_decoder(rep: Report, doc: dict) -> None:
    """The real Swift decoder: PackingPlan.PlanLoader (structural: units, unique item ids,
    contiguous steps) plus PackingPlan.geometryIssues()."""
    binary = os.environ.get("PLAN3D_BIN") or next(
        (str(p) for p in sorted(ROOT.glob("tools/plan3d/.build/*/plan3d")) if p.is_file()), None)
    if not binary:
        rep("Swift decoder (tools/plan3d) loads the plan", False,
            "no plan3d binary: build it with `swift build --package-path tools/plan3d` "
            "(see this file's docstring) or set PLAN3D_BIN")
        return
    out_dir = tempfile.mkdtemp(prefix="pipeline_check_plan3d_")
    plan_path = Path(out_dir) / "server_document.json"
    plan_path.write_text(json.dumps(doc))
    cmd = [binary, str(plan_path), out_dir, "--steps", "--unpacked", "--violations"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    said = next((l for l in tail if l.startswith("plan:")), tail[0] if tail else "")
    rep("Swift decoder (tools/plan3d) loads the plan and reports no geometry issues",
        proc.returncode == 0 and "geometry issues: none" in proc.stdout,
        f"{Path(binary).name} exit={proc.returncode}: {said or '(no output)'}"
        + ("" if proc.returncode == 0 else f"\n        stderr: {proc.stderr.strip()[:400]}"))


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
        # packer3d emits no `nested_in` today, so a real plan never carries a `nestedIn` and
        # the cavity half of the contract is otherwise never exercised. Fabricate one: `nest`
        # declares a cavity that contains the whole intersection (must be PERMITTED),
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
    check_physics_agrees(rep, doc, plan)
    check_swift_decoder(rep, doc)

    print()
    if rep.failures:
        print(f"FAILED {len(rep.failures)} seam(s): " + "; ".join(rep.failures))
        return 1
    print("all seams hold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
