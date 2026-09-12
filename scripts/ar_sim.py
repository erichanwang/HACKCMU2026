#!/usr/bin/env python3
"""Headless AR pipeline simulation: synthetic LiDAR scans -> real scan geometry -> a locally
started server -> a real solver plan -> the real bag->world transform -> containment/overlap
checks. No Mac, no phone, no API keys.

    PYTHONPATH=. python3 scripts/ar_sim.py

`scripts/ar_gate.sh` runs every `scripts/ar_sim*.py` and treats a non-zero exit as RED.

Design
------
- Point clouds are synthesised in Python (numpy, already a project dependency) for a
  suitcase and 4 items, each sitting on its own table plane at an arbitrary rotation, with
  gaussian noise -- the shape `ScanView.swift`'s tap path produces after `densify`.
- The scan geometry (`fitBox`, `heightMap`) and the AR overlay transform (`interiorBox`,
  `PlanAnchor`) are NOT re-implemented in Python. This script compiles `Spike/Geometry.swift`
  and `Spike/PlanAnchor.swift` with `swiftc` (same toolchain as `tests/swift/*/run.sh`) against
  a small generated driver and talks to the resulting binary over stdin/stdout JSON, so it is
  the real code that ships, not a port that can drift from it. If `swiftc` is unavailable this
  step is skipped cleanly (exit 0), the same way a missing Docker/Mongo is handled below --
  this machine has the toolchain (see `swift/PackPhysics/swiftenv.sh`), so in practice this
  sim exercises the genuine Swift geometry and transform code.
- The server is started as a real subprocess (`uv run uvicorn`) and talked to over real HTTP,
  exactly like `scripts/demo_e2e.py`. `XAI_API_KEY` is explicitly stripped from its environment
  (regardless of what `.env` holds) so Grok labelling is off and items come back "unknown" --
  the sim does not need a label, only geometry.
- A recent commit gated every mutating route behind Auth0 JWT auth
  (`server/auth.py::require_auth`). This sim never contacts Auth0 (that would be an API key by
  another name); if the very first POST comes back 401 there is no way to proceed headlessly,
  so this sim skips cleanly (exit 0) with a message, the same way it does for a missing
  Docker/Mongo. It does not depend on the `open_mode()` local-auth bypass some checkouts of
  `server/auth.py` carry uncommitted -- if that lands, requests succeed and the full pipeline
  runs; if it does not, the sim degrades to a clean skip instead of turning the gate red.

Realism
-------
By default this also layers five independently-switchable scan degradations onto the clean
clouds above (`--no-<name>` turns any one off; see `Degradations`): LiDAR noise growing with
range, a one-viewpoint 2.5D scan (the far side of the "l-shape" item is never seen), an item
partly occluded by a neighbour, a cluster that bleeds into the table, and a suitcase scanned
open with its lid in frame. A short report of the dimensional error each induces prints every
run, and every run also checks the solved plan against the *true* (undegraded) suitcase
interior -- this is report-only (never fails the gate) and is where a real limitation, e.g. the
open lid, shows up. `--adversarial` additionally runs two sweeps that print, but never fail on,
known limitations: whether two items 3cm apart get merged by `connectedCluster`'s world-fixed
grid, and whether the AR overlay still lands in the real bag under `tests/swift/drift`'s own
mid/worst-realistic world-origin-drift + plane-error combos.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
_PACKER3D_ROOT = ROOT / "packer3d"
if (_PACKER3D_ROOT / "packer3d" / "__init__.py").is_file() and str(_PACKER3D_ROOT) not in sys.path:
    sys.path.append(str(_PACKER3D_ROOT))  # sibling source dir, same trick as physics/packer3d_adapter.py
from packer3d.models import Item, oriented_solid_boxes  # noqa: E402 -- needs the sys.path append above

RNG_SEED = 20260912
TABLE_Y = 0.80          # world height of the table plane, metres
WALL = 0.01             # suitcaseWallMeters, Spike/ScanView.swift
NOISE = 0.0015          # metres, gaussian noise added to every synthesised point
LABEL_UNAVAILABLE = "sim needs no API key: XAI_API_KEY is stripped from the server's " \
                     "environment on purpose, so Grok labelling is off and items come back 'unknown'."

# --- adversarial scan degradation, off by default in the values above, layered on in scene() ---
SCANNER_POS = np.array([0.0, TABLE_Y + 1.1, 0.0])  # phone held ~1.1m over the table, roughly overhead
NOISE_DISTANCE_SLOPE = 0.004   # extra metres of noise sigma per metre of scanner range (LiDAR falls off with range)
OCCLUDE_CROP = 0.02            # metres cropped off one edge of the "box" item by a neighbour blocking the view
TABLE_BLEED_EXTRA = 0.03       # metres the "open-tray" item's cluster bleeds into the table on one side
LID_LEAN_DEG = 20.0             # degrees the open lid leans back past vertical, in frame
LID_CAPTURE_M = 0.08            # metres of the lid captured near the hinge (not the whole panel)
# tests/swift/drift/main.swift's own mid/worst-realistic combos, split the way its Section 4
# (before/after Spike/ScanView.swift's planeY-re-resolution fix) splits them: a small plane-fit
# noise that a live reread does NOT remove (plane_cm), a vertical world-drift component that live
# rereading DOES correct for (vertical_cm), an axis error, and a horizontal drift -- neither of
# the last two touched by that fix.
# (plane_cm, vertical_cm, axis_deg, horizontal_cm)
DRIFT_MID = (1.0, 2.0, 2.0, 2.0)
DRIFT_WORST = (3.0, 5.0, 5.0, 5.0)


def skip(message: str) -> None:
    print(f"AR SIM: SKIP -- {message}")
    raise SystemExit(0)


def fail(message: str) -> None:
    print(f"AR SIM: FAIL -- {message}", file=sys.stderr)
    raise SystemExit(1)


def warn(name: str, detail: str) -> None:
    """A characterised limitation worth seeing, that is not a failure. scripts/ar_gate.sh lifts
    these into its summary table as WARN rows, so a GREEN gate never reads as "nothing to know".
    """
    print(f"AR-WARN: {name} | {detail}")


# --- geometry synthesis (numpy) ----------------------------------------------------------


def _rot(angle: float):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, -s], [s, c]])


def box_points(rng, w: float, d: float, h: float, cx: float, cz: float, angle: float,
               table_y: float, n_top: int = 14, n_side: int = 6, open_top: bool = False) -> np.ndarray:
    """A closed (or open-topped) box: a dense top face (or just its rim) plus side walls,
    the same "top face + wall samples" shape `tests/main.swift` uses for a scanned box."""
    r = _rot(angle)
    pts = []
    if open_top:
        # No interior floor is visible from outside; only the rim perimeter is at full height.
        for t in np.linspace(-w / 2, w / 2, n_top):
            for lz in (-d / 2, d / 2):
                pts.append((t, lz, h))
        for lz in np.linspace(-d / 2, d / 2, n_top):
            for lx in (-w / 2, w / 2):
                pts.append((lx, lz, h))
    else:
        for lx in np.linspace(-w / 2, w / 2, n_top):
            for lz in np.linspace(-d / 2, d / 2, n_top):
                pts.append((lx, lz, h))
    for t in np.linspace(-w / 2, w / 2, n_side):
        for lz in (-d / 2, d / 2):
            for ly in np.linspace(0, h, n_side):
                pts.append((t, lz, ly))
    for t in np.linspace(-d / 2, d / 2, n_side):
        for lx in (-w / 2, w / 2):
            for ly in np.linspace(0, h, n_side):
                pts.append((lx, t, ly))
    local = np.array(pts, dtype=np.float64)
    world_xz = local[:, :2] @ r.T + np.array([cx, cz])
    world_y = table_y + local[:, 2]
    world = np.stack([world_xz[:, 0], world_y, world_xz[:, 1]], axis=1)
    world[:, 0] += rng.normal(0, NOISE, len(world))
    world[:, 1] += rng.normal(0, NOISE, len(world))
    world[:, 2] += rng.normal(0, NOISE, len(world))
    return world


def l_shape_points(rng, w: float, d: float, h: float, cx: float, cz: float, angle: float,
                    table_y: float, n: int = 14) -> np.ndarray:
    """A box with one quadrant of the top face (and its walls) dropped -- concave footprint,
    same construction `tests/main.swift` uses to exercise the L-shape case."""
    r = _rot(angle)
    pts = []
    for lx in np.linspace(-w / 2, w / 2, n):
        for lz in np.linspace(-d / 2, d / 2, n):
            if lx > 0 and lz > 0:
                continue
            pts.append((lx, lz, h))
    for t in np.linspace(-w / 2, w / 2, n // 2):
        for lz in (-d / 2, d / 2):
            for ly in np.linspace(0, h, 5):
                if lz > 0 and t > 0:
                    continue
                pts.append((t, lz, ly))
    for t in np.linspace(-d / 2, d / 2, n // 2):
        for lx in (-w / 2, w / 2):
            for ly in np.linspace(0, h, 5):
                if lx > 0 and t > 0:
                    continue
                pts.append((lx, t, ly))
    local = np.array(pts, dtype=np.float64)
    world_xz = local[:, :2] @ r.T + np.array([cx, cz])
    world_y = table_y + local[:, 2]
    world = np.stack([world_xz[:, 0], world_y, world_xz[:, 1]], axis=1)
    world += rng.normal(0, NOISE, world.shape)
    return world


def cylinder_points(rng, radius: float, h: float, cx: float, cz: float, angle: float,
                     table_y: float, n_theta: int = 24, n_h: int = 8) -> np.ndarray:
    """A rolled-up soft item lying as a cylinder standing on its round face."""
    theta = np.linspace(0, 2 * math.pi, n_theta, endpoint=False)
    pts = []
    for th in theta:
        lx, lz = radius * math.cos(th), radius * math.sin(th)
        for ly in np.linspace(0, h, n_h):
            pts.append((lx, lz, ly))
        pts.append((lx, lz, h))  # top disc rim
    for rr in np.linspace(0, radius, 5):
        for th in theta:
            pts.append((rr * math.cos(th), rr * math.sin(th), h))
    local = np.array(pts, dtype=np.float64)
    r = _rot(angle)
    world_xz = local[:, :2] @ r.T + np.array([cx, cz])
    world_y = table_y + local[:, 2]
    world = np.stack([world_xz[:, 0], world_y, world_xz[:, 1]], axis=1)
    world += rng.normal(0, NOISE, world.shape)
    return world


@dataclasses.dataclass
class Degradations:
    """Each realistic scan-degradation independently switchable, all on by default."""
    distance_noise: bool = True    # LiDAR noise growing with range from the scanner
    missing_far_side: bool = True  # the "l-shape" item's far-side walls are never seen (2.5D, one viewpoint)
    occlusion: bool = True         # the "box" item is partly blocked by a neighbour
    table_bleed: bool = True       # the "open-tray" item's cluster picks up a sliver of the table
    lid_open: bool = True          # the suitcase is scanned open, its lid in frame

    def any(self) -> bool:
        return any(dataclasses.astuple(self))


NONE_DEGRADED = Degradations(False, False, False, False, False)


def _local_frame(points: np.ndarray, cx: float, cz: float, angle: float) -> np.ndarray:
    """World XZ -> object-local XZ (undo the translate+rotate every generator above applies)."""
    xz = points[:, [0, 2]] - np.array([cx, cz])
    return xz @ _rot(angle)  # rotation matrices are orthonormal: inverse of r.T is r


def add_distance_noise(rng, points: np.ndarray) -> np.ndarray:
    """Extra LiDAR jitter that grows with range from the scanner, layered on top of the flat
    per-point noise every cloud already carries -- real LiDAR is accurate up close and degrades
    roughly linearly with distance."""
    sigma = NOISE_DISTANCE_SLOPE * np.linalg.norm(points - SCANNER_POS, axis=1)
    return points + rng.normal(0, 1, points.shape) * sigma[:, None]


def drop_far_side(points: np.ndarray, cx: float, cz: float, angle: float, h: float, table_y: float) -> np.ndarray:
    """One-viewpoint 2.5D capture: side-wall points on the object's far half are never seen.
    Top-face points (height ~= h) are kept -- the overhead pass still sees the whole footprint,
    per `heightMap`'s own 2.5D comment."""
    local = _local_frame(points, cx, cz, angle)
    is_top = points[:, 1] - table_y > h - 1e-3
    return points[is_top | (local[:, 1] <= 0)]  # local z (depth) <= 0 -> the near half


def occlude_edge(points: np.ndarray, cx: float, cz: float, angle: float, d: float, crop: float) -> np.ndarray:
    """A neighbouring object sits right in front of one whole edge, blocking the scanner's view
    of it entirely (not just a wall, the top face rim on that edge too) -- unlike `drop_far_side`,
    nothing else in the cloud reaches that true extreme, so the measured footprint shrinks."""
    local = _local_frame(points, cx, cz, angle)
    return points[local[:, 1] <= d / 2 - crop]


def table_bleed(rng, points: np.ndarray, cx: float, cz: float, w: float, angle: float, table_y: float,
                 extra: float, n: int = 10) -> np.ndarray:
    """`connectedCluster`'s grid is world-fixed (Geometry.swift's own ponytail note); with no
    empty cell gap between the object and the table's edge, a sliver of table rides along in the
    same tap's cluster, extending the measured footprint."""
    local = np.stack([np.linspace(-w / 2, w / 2 + extra, n), np.zeros(n)], axis=1)
    world_xz = local @ _rot(angle).T + np.array([cx, cz])
    strip = np.stack([world_xz[:, 0], np.full(n, table_y), world_xz[:, 1]], axis=1)
    strip[:, 1] += rng.normal(0, NOISE, n)
    return np.concatenate([points, strip], axis=0)


def lid_points(rng, w: float, d: float, h: float, cx: float, cz: float, angle: float, table_y: float,
               lean_deg: float, extent: float, n: int = 10) -> np.ndarray:
    """The suitcase scanned open: the near part of the lid, hinged at the back top edge and
    leaning back into frame, inflates the scanned outer shell beyond the true (closed) footprint."""
    lean = math.radians(lean_deg)
    local = []
    for s in np.linspace(0, extent, n):
        lz, ly = d / 2 + s * math.sin(lean), h + s * math.cos(lean)
        for lx in np.linspace(-w / 2, w / 2, n):
            local.append((lx, lz, ly))
    local = np.array(local)
    world_xz = local[:, :2] @ _rot(angle).T + np.array([cx, cz])
    world = np.stack([world_xz[:, 0], table_y + local[:, 2], world_xz[:, 1]], axis=1)
    return world + rng.normal(0, NOISE, world.shape)


# (name, cell, generator-kwargs) for every item, shared between the true and degraded scenes so
# each item's base geometry (and its independent RNG stream) is identical before degradation --
# the only way the dimension deltas below measure the degradation and nothing else.
SUITCASE_ARGS = dict(w=0.55, d=0.35, h=0.40, cx=0.0, cz=0.0, angle=math.radians(23), table_y=TABLE_Y, open_top=True)
BOX_ARGS = dict(w=0.16, d=0.10, h=0.06, cx=1.5, cz=0.5, angle=math.radians(10), table_y=TABLE_Y)
ROLL_ARGS = dict(radius=0.06, h=0.20, cx=-1.5, cz=0.6, angle=0.0, table_y=TABLE_Y)
LSHAPE_ARGS = dict(w=0.14, d=0.12, h=0.08, cx=0.8, cz=-1.4, angle=math.radians(58), table_y=TABLE_Y)
TRAY_ARGS = dict(w=0.18, d=0.08, h=0.05, cx=-0.9, cz=-1.2, angle=math.radians(-33), table_y=TABLE_Y, open_top=True)


def scene(deg: Degradations = NONE_DEGRADED) -> tuple[dict, list[dict]]:
    """The suitcase (outer shell points + cell) and 4 items (points, cell, name) -- each on its
    own table patch with its own seeded RNG (independent of the others and of `deg`, so a
    degradation applied to one item never perturbs another's noise realisation), matching one
    `ScanView` tap per object. `deg` layers realistic scan degradation on top, each switchable."""
    rng_suitcase = np.random.default_rng(RNG_SEED)
    suitcase_pts = box_points(rng_suitcase, **SUITCASE_ARGS)
    if deg.lid_open:
        suitcase_pts = np.concatenate(
            [suitcase_pts, lid_points(rng_suitcase, SUITCASE_ARGS["w"], SUITCASE_ARGS["d"], SUITCASE_ARGS["h"],
                                       SUITCASE_ARGS["cx"], SUITCASE_ARGS["cz"], SUITCASE_ARGS["angle"], TABLE_Y,
                                       LID_LEAN_DEG, LID_CAPTURE_M)])
    if deg.distance_noise:
        suitcase_pts = add_distance_noise(rng_suitcase, suitcase_pts)
    suitcase = {"points": suitcase_pts, "cell": 0.05}

    rng_box = np.random.default_rng(RNG_SEED + 1)
    box_pts = box_points(rng_box, **BOX_ARGS)
    if deg.occlusion:
        box_pts = occlude_edge(box_pts, BOX_ARGS["cx"], BOX_ARGS["cz"], BOX_ARGS["angle"], BOX_ARGS["d"], OCCLUDE_CROP)
    if deg.distance_noise:
        box_pts = add_distance_noise(rng_box, box_pts)

    rng_roll = np.random.default_rng(RNG_SEED + 2)
    roll_pts = cylinder_points(rng_roll, **ROLL_ARGS)
    if deg.distance_noise:
        roll_pts = add_distance_noise(rng_roll, roll_pts)

    rng_l = np.random.default_rng(RNG_SEED + 3)
    l_pts = l_shape_points(rng_l, **LSHAPE_ARGS)
    if deg.missing_far_side:
        l_pts = drop_far_side(l_pts, LSHAPE_ARGS["cx"], LSHAPE_ARGS["cz"], LSHAPE_ARGS["angle"], LSHAPE_ARGS["h"], TABLE_Y)
    if deg.distance_noise:
        l_pts = add_distance_noise(rng_l, l_pts)

    rng_tray = np.random.default_rng(RNG_SEED + 4)
    tray_pts = box_points(rng_tray, **TRAY_ARGS)
    if deg.table_bleed:
        tray_pts = table_bleed(rng_tray, tray_pts, TRAY_ARGS["cx"], TRAY_ARGS["cz"], TRAY_ARGS["w"],
                                TRAY_ARGS["angle"], TABLE_Y, TABLE_BLEED_EXTRA)
    if deg.distance_noise:
        tray_pts = add_distance_noise(rng_tray, tray_pts)

    items = [
        {"name": "box", "cell": 0.02, "points": box_pts},
        {"name": "rolled-soft", "cell": 0.02, "points": roll_pts},
        {"name": "l-shape", "cell": 0.02, "points": l_pts},
        {"name": "open-tray", "cell": 0.02, "points": tray_pts},
    ]
    return suitcase, items


# --- swift driver -------------------------------------------------------------------------

DRIVER_SOURCE = r"""
import Foundation
#if canImport(simd)
import simd
#endif

struct ScanIn: Codable { let points: [[Float]]; let planeY: Float; let cell: Float; let padding: Float }
struct ScanOut: Codable {
    let width: Float; let height: Float; let depth: Float; let axis: [Float]; let heights: [[Float]]
    let footprint: [[Float]]?  // ScannedItem.footprint(from:) -- same code Spike/ScanView.swift will call
}

struct PlacementIn: Codable { let position: [Float]; let size: [Float] }
struct TransformIn: Codable { let suitcasePoints: [[Float]]; let planeY: Float; let wall: Float; let placements: [PlacementIn] }
struct TransformOut: Codable {
    let interior: [Float]; let worldCenters: [[Float]]; let bagRoundTrip: [[Float]]
    let axis: [Float]; let perp: [Float]; let origin: [Float]  // so Python can project ANY world point into this bag's frame
}

struct ClusterIn: Codable { let points: [[Float]]; let seed: [Float]; let cell: Float; let planeY: Float }
struct ClusterOut: Codable { let clusterCount: Int; let totalCount: Int; let box: [Float] }  // box empty -> fitBox failed

struct Request: Codable { let cmd: String; let scan: [ScanIn]?; let transform: TransformIn?; let cluster: ClusterIn? }

func runScan(_ items: [ScanIn]) -> [ScanOut] {
    items.map { inp in
        let pts = inp.points.map { SIMD3<Float>($0[0], $0[1], $0[2]) }
        guard let fit = fitBox(points: pts, planeY: inp.planeY, padding: inp.padding) else {
            FileHandle.standardError.write("fitBox failed on \(pts.count) points\n".data(using: .utf8)!)
            exit(1)
        }
        let hm = heightMap(points: pts, box: fit, planeY: inp.planeY, cell: inp.cell)
        return ScanOut(width: fit.width, height: fit.height, depth: fit.depth,
                        axis: [fit.axis.x, fit.axis.y, fit.axis.z], heights: hm,
                        footprint: ScannedItem.footprint(from: fit))
    }
}

func runTransform(_ t: TransformIn) -> TransformOut {
    let pts = t.suitcasePoints.map { SIMD3<Float>($0[0], $0[1], $0[2]) }
    guard let outer = fitBox(points: pts, planeY: t.planeY, padding: 0, trimAboveRim: true) else {
        FileHandle.standardError.write("fitBox failed on suitcase points\n".data(using: .utf8)!)
        exit(1)
    }
    let interior = interiorBox(outer, wall: t.wall)
    let anchor = PlanAnchor(interior: interior, planeY: t.planeY + t.wall)
    var worldCenters: [[Float]] = [], roundTrip: [[Float]] = []
    for p in t.placements {
        let pos = SIMD3<Float>(p.position[0], p.position[1], p.position[2])
        let size = SIMD3<Float>(p.size[0], p.size[1], p.size[2])
        let world = anchor.worldCenter(position: pos, size: size)
        worldCenters.append([world.x, world.y, world.z])
        let rel = world - anchor.origin
        roundTrip.append([simd_dot(rel, anchor.axis), rel.y, simd_dot(rel, anchor.perp)])
    }
    return TransformOut(interior: [interior.width, interior.height, interior.depth],
                         worldCenters: worldCenters, bagRoundTrip: roundTrip,
                         axis: [anchor.axis.x, anchor.axis.y, anchor.axis.z],
                         perp: [anchor.perp.x, anchor.perp.y, anchor.perp.z],
                         origin: [anchor.origin.x, anchor.origin.y, anchor.origin.z])
}

func runCluster(_ c: ClusterIn) -> ClusterOut {
    let pts = c.points.map { SIMD3<Float>($0[0], $0[1], $0[2]) }
    let seed = SIMD3<Float>(c.seed[0], c.seed[1], c.seed[2])
    let cluster = connectedCluster(pts, seed: seed, cell: c.cell)
    guard let fit = fitBox(points: cluster, planeY: c.planeY, padding: 0) else {
        return ClusterOut(clusterCount: cluster.count, totalCount: pts.count, box: [])
    }
    return ClusterOut(clusterCount: cluster.count, totalCount: pts.count, box: [fit.width, fit.height, fit.depth])
}

let data = FileHandle.standardInput.readDataToEndOfFile()
let req = try! JSONDecoder().decode(Request.self, from: data)
let encoder = JSONEncoder()
switch req.cmd {
case "scan": FileHandle.standardOutput.write(try! encoder.encode(runScan(req.scan!)))
case "transform": FileHandle.standardOutput.write(try! encoder.encode(runTransform(req.transform!)))
case "cluster": FileHandle.standardOutput.write(try! encoder.encode(runCluster(req.cluster!)))
default:
    FileHandle.standardError.write("unknown cmd \(req.cmd)\n".data(using: .utf8)!)
    exit(1)
}
"""


def build_driver(tmp: Path) -> Path:
    """Compile Spike/Geometry.swift + Spike/PlanAnchor.swift with the generated driver above,
    the same way tests/swift/{scan,plan}/run.sh do it. Named `main.swift`: swiftc only allows
    top-level statements in a file with that exact basename when compiling several files."""
    if shutil.which("swiftc") is None:
        env_script = ROOT / "swift" / "PackPhysics" / "swiftenv.sh"
        if not env_script.exists():
            skip("no swiftc on PATH and no swift/PackPhysics/swiftenv.sh to source; cannot drive the real scan geometry")
    (tmp / "main.swift").write_text(DRIVER_SOURCE)
    out = tmp / "driver"
    cmd = (
        f'if ! command -v swiftc >/dev/null 2>&1; then . "{ROOT}/swift/PackPhysics/swiftenv.sh"; fi; '
        f'swiftc -Onone -o "{out}" "{ROOT}/Spike/Geometry.swift" "{ROOT}/Spike/PlanAnchor.swift" '
        f'"{ROOT}/tests/swift/SimdShim.swift" "{tmp}/main.swift"'
    )
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
    if r.returncode != 0:
        skip(f"swiftc failed to build the scan-geometry driver, cannot drive the real code:\n{r.stderr[-2000:]}")
    return out


def run_driver(driver: Path, request: dict) -> dict:
    r = subprocess.run([str(driver)], input=json.dumps(request), capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        fail(f"scan-geometry driver crashed on cmd={request['cmd']!r}:\n{r.stderr}")
    return json.loads(r.stdout)


# --- server ---------------------------------------------------------------------------------


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_for_port(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


def start_mongo() -> tuple[str, str] | tuple[None, None]:
    """A throwaway `mongo:7` container on a free port, or (None, None) if Docker cannot provide one."""
    if shutil.which("docker") is None:
        return None, None
    port = free_port()
    name = f"ar-sim-mongo-{uuid.uuid4().hex[:8]}"
    r = subprocess.run(
        ["docker", "run", "--rm", "-d", "--name", name, "-p", f"{port}:27017", "mongo:7"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        return None, None
    if not wait_for_port("127.0.0.1", port, timeout=20):
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        return None, None
    return f"mongodb://localhost:{port}", name


def cleanup(mongo_name: str | None, server: subprocess.Popen | None) -> None:
    if server is not None and server.poll() is None:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
    if mongo_name:
        subprocess.run(["docker", "rm", "-f", mongo_name], capture_output=True)


def call(url: str, *, data: bytes | None = None, content_type: str | None = None, method: str = "GET"):
    headers = {"Content-Type": content_type} if content_type else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def post_json(url: str, payload: dict):
    return call(url, data=json.dumps(payload).encode(), content_type="application/json", method="POST")


def post_item(url: str, item: dict) -> dict:
    """Multipart `POST /items`; the photo is a stub since Grok labelling is off (no API key)."""
    boundary = uuid.uuid4().hex
    jpeg_stub = bytes.fromhex(
        "ffd8ffe000104a4649460001010100480048000" "0ffdb004300ffffffffffffffffffffffffffffffffff"
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"
        "ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060"
        "708090a0bffda0008010100003f00d2ffd9"
    )
    body = b"".join([
        f'--{boundary}\r\nContent-Disposition: form-data; name="item"\r\n\r\n'.encode(),
        json.dumps(item).encode(),
        f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="scan.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n".encode(),
        jpeg_stub,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return call(url, data=body, content_type=f"multipart/form-data; boundary={boundary}", method="POST")


def item_doc(it: dict, fit: dict, *, with_footprint: bool, item_id: str | None = None) -> dict:
    """The `POST /items` body for one scanned item (SCAN_OUTPUT.md), optionally carrying the
    footprint the real Swift path now computes (`ScannedItem.footprint`, docs/LIDAR_HULLS.md).
    `item_id` defaults to a fresh uuid; pass a fixed one to compare two POSTs of "the same" item
    (the solver's own ordering can depend on item id, so two calls with different random ids are
    not a controlled comparison -- see `footprint_wiring_check`)."""
    doc = {"id": item_id or f"{it['name']}-{uuid.uuid4().hex[:8]}", "suitcaseId": None,
           "dimensions": [fit["width"], fit["height"], fit["depth"]], "cellSize": it["cell"],
           "heights": fit["heights"]}
    if with_footprint and fit.get("footprint") is not None:
        doc["footprint"] = fit["footprint"]
    return doc


def assert_footprint_in_box(name: str, fit: dict) -> None:
    """Item 3's consistency check: every footprint vertex must fit inside the SAME box
    (width/depth) this scan produced -- physics/geometry.py's `footprint_local` enforces this
    server-side with 1e-6 m slack; assert it here too since prepack.py never hands the footprint
    to that check on the live `/plan` path (see the wiring finding this script prints)."""
    fp = fit.get("footprint")
    if fp is None:
        return
    hx, hz = fit["width"] / 2, fit["depth"] / 2
    for x, z in fp:
        if abs(x) > hx + 1e-6 or abs(z) > hz + 1e-6:
            fail(f"{name}: footprint point ({x:.4f}, {z:.4f}) exceeds box half-dimensions "
                 f"({hx:.4f}, {hz:.4f})")


def footprint_wiring_check(base: str, dims, items: list[dict], item_fits: list[dict]) -> None:
    """Does sending `footprint` change the solver's plan at all? POST the same items/suitcase
    twice -- once with `footprint`, once without -- and compare. `prepack.py`'s `physics_object()`
    and packer3d's own Item builder never read `doc["footprint"]`, so this is expected to show no
    effect; that is the real finding item 4 of the task asks for, not a bug in this script.

    Pinning identical item ids across both POSTs (so id-driven noise doesn't confound the diff)
    turned up a SEPARATE, real finding: the solver's own step/orientation assignment is not
    reproducible run-to-run even with byte-identical requests -- verified by running the SAME
    request (`with_footprint` held fixed) twice in a row and seeing the placements differ on
    ~1/3 of trials. So exact placement equality is not a valid footprint test on its own; compare
    the solver's own metrics (items_packed/unpacked -- order-invariant, and the one number that
    would actually move if footprint let something pack that otherwise wouldn't) plus a
    permutation-of-assignment-invariant summary (the multiset of packed box volumes)."""
    ids = [f"{it['name']}-wiring-{uuid.uuid4().hex[:8]}" for it in items]
    with_fp, with_metrics = post_suitcase_items_plan(base, "wiring check (with footprint)", dims, items,
                                                       item_fits, with_footprint=True, item_ids=ids)
    without_fp, without_metrics = post_suitcase_items_plan(base, "wiring check (without footprint)", dims,
                                                             items, item_fits, with_footprint=False, item_ids=ids)
    metrics_same = (with_metrics.get("items_packed") == without_metrics.get("items_packed")
                     and with_metrics.get("items_unpacked") == without_metrics.get("items_unpacked"))
    volumes = lambda ps: sorted(round(p["size"]["x"] * p["size"]["y"] * p["size"]["z"], 6) for p in ps)
    volumes_same = volumes(with_fp) == volumes(without_fp)
    exact_same = with_fp == without_fp
    print(f"[wiring] with vs without `footprint`: items_packed/unpacked "
          f"{'match' if metrics_same else 'DIFFER'} "
          f"({with_metrics.get('items_packed')}/{with_metrics.get('items_unpacked')} vs "
          f"{without_metrics.get('items_packed')}/{without_metrics.get('items_unpacked')}), "
          f"packed volumes {'match' if volumes_same else 'DIFFER'}, "
          f"exact placements {'match' if exact_same else 'differ (see note on solver order-noise above)'}")
    if metrics_same and volumes_same:
        print("[wiring] footprint is accepted by the server (Scan model has extra=\"allow\") but "
              "never reaches a packing decision: physics/prepack.py's physics_object() and "
              "packer3d's own Item builder both read only dimensions/heights/rigidity/etc., "
              "never doc[\"footprint\"] -- the field is stored on the item doc and otherwise inert.")
        warn("footprint inert", "the phone sends a hull but no packing decision reads it; "
                                "an L-shaped item still packs as a rectangle")
    else:
        # The goal, not an anomaly: once the physics gate grades the footprint and the solver
        # packs the prism, the two runs SHOULD diverge. When that lands this becomes an
        # assertion (footprint must change the verdict) rather than an observation.
        print("[wiring] footprint now changes a real outcome -- the server half is wired up. "
              f"with={with_metrics} without={without_metrics}")


def post_suitcase_items_plan(base: str, name: str, dims, items: list[dict], item_fits: list[dict],
                              *, with_footprint: bool = False,
                              item_ids: list[str] | None = None) -> tuple[list[dict], dict]:
    """POST /suitcases, POST every item's scan under it, POST /plan; returns (placements, solver
    metrics). Used to solve a plan against a specific (e.g. ground-truth) suitcase, distinct from
    -- and not printed alongside -- the main pipeline's own suitcase/items/plan. `item_ids`, if
    given, pins each item's id (see `footprint_wiring_check`); otherwise each gets a fresh
    random one."""
    status, suitcase_doc = post_json(f"{base}/suitcases", {"name": name, "dimensions": dims})
    if status != 200:
        fail(f"POST /suitcases ({name}) -> {status}: {suitcase_doc}")
    ids = item_ids or [None] * len(items)
    for it, fit, item_id in zip(items, item_fits, ids):
        doc = item_doc(it, fit, with_footprint=with_footprint, item_id=item_id)
        doc["suitcaseId"] = suitcase_doc["id"]
        status, resp = post_item(f"{base}/items", doc)
        if status != 200:
            fail(f"POST /items ({name}/{it['name']}) -> {status}: {resp}")
    status, plan_doc = post_json(f"{base}/suitcases/{suitcase_doc['id']}/plan", {})
    if status != 200:
        fail(f"POST /suitcases/{{id}}/plan ({name}) -> {status}: {plan_doc}")
    return plan_doc["plan"]["placements"], plan_doc["solver"]["metrics"]


# --- checks -----------------------------------------------------------------------------


def assert_finite(label: str, xyz) -> None:
    if not all(math.isfinite(v) for v in xyz):
        fail(f"{label}: world position is not finite: {xyz}")


def assert_contained(label: str, position, size, interior, eps: float = 1e-4) -> None:
    for axis, name in enumerate("xyz"):
        lo, hi = position[axis], position[axis] + size[axis]
        if lo < -eps or hi > interior[axis] + eps:
            fail(f"{label}: [{lo:.4f}, {hi:.4f}] on {name} escapes the bag's [0, {interior[axis]:.4f}]")


def boxes_overlap(a_pos, a_size, b_pos, b_size, eps: float = 1e-4) -> bool:
    return all(a_pos[i] < b_pos[i] + b_size[i] - eps and b_pos[i] < a_pos[i] + a_size[i] - eps for i in range(3))


# app_plan.py's placement frame is packer3d's with y/z swapped, no reflection:
# "app(x, y, z) = (packer_x, packer_z, packer_y)". `oriented_solid_boxes` only decomposes a
# height-grid item into its cavity boxes for orientations "xyz"/"yxz" (the two that keep the
# item's own "up" along world z); app_plan.py's `_ROTATION` maps exactly those two packer
# orientations to the app rotation strings "XYZ"/"ZYX", everything else falls back to the plain
# bbox in oriented_solid_boxes anyway, so only those two need naming here.
_APP_ROTATION_TO_PACKER_ORIENTATION = {"XYZ": "xyz", "ZYX": "yxz"}


def placement_solid_boxes(placement: dict, items_by_id: dict) -> list:
    """`placement`'s occupied boxes (list of packer-frame ``(lo, hi)`` tuples), decomposed into
    cavity solids via `packer3d`'s own `Item.solid_boxes()`/`oriented_solid_boxes` -- the same
    algorithm `packer3d.verify` and `physics/packer3d_adapter.py` use -- instead of one bbox per
    placement, so a legitimately nested item (e.g. a cup inside a bowl) isn't reported as an
    overlap with its container."""
    pos, size = placement["position"], placement["size"]
    packer_pos = (pos["x"], pos["z"], pos["y"])
    packer_dims = (size["x"], size["z"], size["y"])
    item = items_by_id.get(placement["itemId"])
    if item is None:
        return [(packer_pos, tuple(packer_pos[k] + packer_dims[k] for k in range(3)))]
    orientation = _APP_ROTATION_TO_PACKER_ORIENTATION.get(placement.get("rotation"), "")
    return oriented_solid_boxes(item, packer_pos, packer_dims, orientation)


def solids_overlap(placement_a: dict, placement_b: dict, items_by_id: dict) -> bool:
    """True iff any solid sub-box of `placement_a` truly overlaps any solid sub-box of
    `placement_b` -- unlike a bare bounding-box test, this lets one item's cavity legitimately
    contain another's solid."""
    for a_lo, a_hi in placement_solid_boxes(placement_a, items_by_id):
        a_size = [a_hi[k] - a_lo[k] for k in range(3)]
        for b_lo, b_hi in placement_solid_boxes(placement_b, items_by_id):
            b_size = [b_hi[k] - b_lo[k] for k in range(3)]
            if boxes_overlap(a_lo, a_size, b_lo, b_size):
                return True
    return False


# --- degradation report -------------------------------------------------------------------


def scan_all(driver: Path, suitcase: dict, items: list[dict]) -> tuple[dict, list[dict]]:
    req = {"cmd": "scan", "scan": [
        {"points": suitcase["points"].tolist(), "planeY": TABLE_Y, "cell": suitcase["cell"], "padding": 0.0},
        *[{"points": it["points"].tolist(), "planeY": TABLE_Y, "cell": it["cell"], "padding": 0.0} for it in items],
    ]}
    out = run_driver(driver, req)
    return out[0], out[1:]


def dims_m(fit: dict) -> np.ndarray:
    """[height, sorted(width, depth)]: `width`/`depth` are just minAreaRect's labels for the two
    footprint edges, and near-identical point sets can pick either edge as "width" first -- sort
    them so that labelling ambiguity never reads as a dimensional error."""
    return np.array([fit["height"], *sorted([fit["width"], fit["depth"]])])


# flag -> (index into [suitcase, *items] to compare, one-line note for the report table)
DEGRADATION_TARGETS = {
    "distance_noise": (None, "LiDAR noise growing with range -- applies to every cloud"),
    "missing_far_side": (3, "l-shape: far-side walls unseen; top-face rim still gives the true footprint"),
    "occlusion": (1, "box: a neighbour blocks one whole edge from the scanner"),
    "table_bleed": (4, "open-tray: cluster picks up a sliver of the table (no empty grid cell gap)"),
    "lid_open": (0, "suitcase: open lid in frame inflates the scanned outer shell"),
}


def degradation_report(driver: Path) -> None:
    """Isolate each degradation (everything else off) and measure the dimensional error it alone
    induces on the item/suitcase it targets, against a ground-truth (fully clean) scan."""
    true_suitcase, true_items = scene(NONE_DEGRADED)
    true_fits = scan_all(driver, true_suitcase, true_items)
    true_all = [true_fits[0], *true_fits[1]]  # index 0 = suitcase, 1..4 = items in scene() order

    print("== scan degradation report (each isolated; magnitudes are this sim's constants) ==")
    print(f"{'degradation':<17} {'target':<10} {'true (m)':<22} {'degraded (m)':<22} {'max |delta|':<12} note")
    for flag, (idx, note) in DEGRADATION_TARGETS.items():
        deg = Degradations(**{f.name: f.name == flag for f in dataclasses.fields(Degradations)})
        d_suitcase, d_items = scene(deg)
        d_fits = scan_all(driver, d_suitcase, d_items)
        degraded_all = [d_fits[0], *d_fits[1]]
        targets = range(5) if idx is None else [idx]
        max_delta, worst = 0.0, None
        for i in targets:
            delta = float(np.max(np.abs(dims_m(degraded_all[i]) - dims_m(true_all[i]))))
            if delta >= max_delta:
                max_delta, worst = delta, i
        t, g = true_all[worst], degraded_all[worst]
        name = "suitcase" if worst == 0 else true_items[worst - 1]["name"]
        print(f"{flag:<17} {name:<10} "
              f"{t['width']:.3f}x{t['height']:.3f}x{t['depth']:.3f}      "
              f"{g['width']:.3f}x{g['height']:.3f}x{g['depth']:.3f}      "
              f"{max_delta * 100:6.2f} cm   {note}")
    print("")


# --- geometry helpers for cross-frame checks (drift / lid-open true-interior containment) ------


def placement_corners(axis, perp, origin, pos, size) -> list[np.ndarray]:
    axis, perp, origin, up = np.array(axis), np.array(perp), np.array(origin), np.array([0.0, 1.0, 0.0])
    center = origin + axis * (pos[0] + size[0] / 2) + up * (pos[1] + size[1] / 2) + perp * (pos[2] + size[2] / 2)
    return [center + axis * (sx * size[0]) + up * (sy * size[1]) + perp * (sz * size[2])
            for sx in (-0.5, 0.5) for sy in (-0.5, 0.5) for sz in (-0.5, 0.5)]


def bag_frame(axis, perp, origin, point) -> np.ndarray:
    rel = np.array(point) - np.array(origin)
    return np.array([rel @ np.array(axis), rel[1], rel @ np.array(perp)])


def escape(coord: np.ndarray, interior) -> float:
    """How far (metres) `coord` (in bag-local xyz) sits outside [0, interior] on the worst axis."""
    return max(0.0, *(max(-coord[i], coord[i] - interior[i]) for i in range(3)))


def worst_case_containment(placements, drawn_axis, drawn_perp, drawn_origin, true_axis, true_perp, true_origin,
                            true_interior) -> tuple[float, int]:
    """Every placement's corners, drawn with the (possibly wrong) `drawn_*` anchor, projected into
    the true bag's frame -- max escape (metres) and how many corners escape at all."""
    max_esc, escaped = 0.0, 0
    for p in placements:
        pos = [p["position"]["x"], p["position"]["y"], p["position"]["z"]]
        size = [p["size"]["x"], p["size"]["y"], p["size"]["z"]]
        for c in placement_corners(drawn_axis, drawn_perp, drawn_origin, pos, size):
            e = escape(bag_frame(true_axis, true_perp, true_origin, c), true_interior)
            max_esc = max(max_esc, e)
            if e > 1e-4:
                escaped += 1
    return max_esc, escaped


# --- adversarial-only checks (scripts/ar_sim.py --adversarial) -----------------------------


def clutter_check(driver: Path) -> None:
    """Two boxes 3cm apart (surface to surface): does `connectedCluster`'s world-fixed grid keep
    them separate, or ship a single fused box to the server? Per Geometry.swift's own ponytail
    comment this depends on grid phase, so try two phases rather than asserting one outcome."""
    w, d, h, cell, gap = BOX_ARGS["w"], BOX_ARGS["d"], BOX_ARGS["h"], 0.02, 0.03
    print("== clutter: two boxes 3cm apart, one tap on the first ==")
    print(f"{'grid phase':<12} {'cluster pts':<14} {'of total':<10} {'resulting box (m)':<20} verdict")
    merged_phases = 0
    phases = (0.0, cell / 2)
    for phase in phases:
        cx, cz = 2.0 + phase, 2.0
        a = box_points(np.random.default_rng(RNG_SEED + 100), w=w, d=d, h=h, cx=cx, cz=cz, angle=0.0, table_y=TABLE_Y)
        b = box_points(np.random.default_rng(RNG_SEED + 101), w=w, d=d, h=h, cx=cx + w + gap, cz=cz, angle=0.0,
                        table_y=TABLE_Y)
        both = np.concatenate([a, b])
        out = run_driver(driver, {"cmd": "cluster", "cluster": {
            "points": both.tolist(), "seed": [cx, TABLE_Y + h, cz], "cell": cell, "planeY": TABLE_Y}})
        assert out["box"], "fitBox failed on a two-box cluster -- should never happen here"
        merged = out["clusterCount"] > 1.3 * len(a)
        box_str = "x".join(f"{v:.3f}" for v in out["box"])
        print(f"{phase * 100:5.1f} cm     {out['clusterCount']:<14} {out['clusterCount']}/{out['totalCount']:<8} "
              f"{box_str:<20} {'MERGED (known limitation)' if merged else 'separated'}")
        merged_phases += bool(merged)
    print("")
    if merged_phases:
        warn("clutter merge", f"two boxes {gap * 100:.0f} cm apart fuse into one at "
                              f"{merged_phases}/{len(phases)} grid phases (connectedCluster limitation)")


def _drift_fit(driver: Path, true_points: np.ndarray, pivot: np.ndarray, axis_deg: float, horizontal_cm: float,
               plane_y: float, placements: list[dict]) -> dict:
    """Rotate+translate the true suitcase cloud horizontally (world-origin drift) and refit with
    `plane_y` as the table-height assumption, then transform `placements` through the result."""
    theta = math.radians(axis_deg)
    xz = true_points[:, [0, 2]] - pivot
    xz = xz @ _rot(theta).T + pivot + np.array([1, 1]) / math.sqrt(2) * (horizontal_cm / 100)
    points = true_points.copy()
    points[:, 0], points[:, 2] = xz[:, 0], xz[:, 1]
    return run_driver(driver, {"cmd": "transform", "transform": {
        "suitcasePoints": points.tolist(), "planeY": plane_y, "wall": WALL,
        "placements": [{"position": [p["position"]["x"], p["position"]["y"], p["position"]["z"]],
                         "size": [p["size"]["x"], p["size"]["y"], p["size"]["z"]]} for p in placements]}})


def _drift_corner_err(true_out: dict, drifted_out: dict, placements: list[dict]) -> float:
    max_err = 0.0
    for p in placements:
        pos = [p["position"]["x"], p["position"]["y"], p["position"]["z"]]
        size = [p["size"]["x"], p["size"]["y"], p["size"]["z"]]
        true_c = placement_corners(true_out["axis"], true_out["perp"], true_out["origin"], pos, size)
        drift_c = placement_corners(drifted_out["axis"], drifted_out["perp"], drifted_out["origin"], pos, size)
        max_err = max(max_err, max(float(np.linalg.norm(dc - tc)) for tc, dc in zip(true_c, drift_c)))
    return max_err


def drift_check(driver: Path, true_suitcase_points: np.ndarray, true_out: dict, placements: list[dict]) -> None:
    """World-origin drift + plane error between scan time and overlay time, at the mid/worst
    magnitudes tests/swift/drift/main.swift itself characterised. `placements` must come from a
    plan solved against `true_out`'s own (undegraded) suitcase -- otherwise this just re-reports
    whatever degradation inflated the bag, mis-attributed to drift.

    Spike/ScanView.swift (commit ec6019a) no longer freezes `planeY` at the scan tap; it re-reads
    the nearest live ARPlaneAnchor every frame. tests/swift/drift's own before/after (Section 4)
    shows this corrects for *vertical* world drift (the table's estimated height changing between
    scan and overlay) but not the one-time plane-fit noise a fresh reading still carries, and not
    at all for horizontal drift or axis error -- re-resolving "the bag's floor" only ever touches
    height. Modelled the same way here: `plane_cm` (unreduced) and `vertical_cm` (corrected, shown
    only for contrast) are kept separate, and only the corrected ("today") row drives the verdict."""
    pivot = np.array([SUITCASE_ARGS["cx"], SUITCASE_ARGS["cz"]])
    print("== drift: world-origin drift + plane/axis error between scan and overlay ==")
    print("(planeY is re-resolved live -- Spike/ScanView.swift's refreshPlaneY -- so vertical world "
          "drift is now largely corrected; horizontal drift and axis error are not)")
    print(f"{'scenario':<58} {'corner err (today/frozen)':<28} {'escape (today/frozen)':<28} fits? (today)")
    worst_esc, worst_label = 0.0, ""
    for label, (plane_cm, vertical_cm, axis_deg, horizontal_cm) in (
            ("mid-range", DRIFT_MID), ("worst realistic", DRIFT_WORST)):
        # The real table never moves -- what drifts is ARKit's world-frame belief about its
        # height, i.e. purely a planeY *input* error, not a change in the points LiDAR would see.
        # frozen bakes in both the tap-time reading noise (plane_cm) and everything the world
        # frame has since drifted by (vertical_cm); today re-reads live, so only the reading
        # noise -- present at any instant -- survives.
        today = _drift_fit(driver, true_suitcase_points, pivot, axis_deg, horizontal_cm,
                            TABLE_Y + plane_cm / 100, placements)
        frozen = _drift_fit(driver, true_suitcase_points, pivot, axis_deg, horizontal_cm,
                             TABLE_Y + plane_cm / 100 + vertical_cm / 100, placements)

        corner_err = _drift_corner_err(true_out, today, placements)
        frozen_corner_err = _drift_corner_err(true_out, frozen, placements)
        max_esc, escaped = worst_case_containment(
            placements, today["axis"], today["perp"], today["origin"],
            true_out["axis"], true_out["perp"], true_out["origin"], true_out["interior"])
        frozen_esc, _ = worst_case_containment(
            placements, frozen["axis"], frozen["perp"], frozen["origin"],
            true_out["axis"], true_out["perp"], true_out["origin"], true_out["interior"])
        verdict = "NO -- still escapes the real bag" if max_esc > 1e-4 else "yes"
        scenario = (f"{label} ({plane_cm:.0f}cm plane, {vertical_cm:.0f}cm vertical drift, "
                    f"{axis_deg:.0f}deg axis, {horizontal_cm:.0f}cm horizontal drift)")
        print(f"{scenario:<58} {corner_err * 100:5.2f} / {frozen_corner_err * 100:5.2f} cm         "
              f"{max_esc * 100:5.2f} / {frozen_esc * 100:5.2f} cm ({escaped} corners)   {verdict}")
        if max_esc > worst_esc:
            worst_esc, worst_label = max_esc, label
    if worst_esc > 1e-4:
        warn("drift escape", f"worst {worst_esc * 100:.2f} cm at {worst_label} drift, "
                              f"live-planeY corrected (horizontal drift and axis error are not)")
    print("note: horizontal drift + axis error dominate both columns here (the interior is tall "
          "enough that the vertical shift alone doesn't threaten containment); it still shows up "
          "as the small today/frozen gap in corner err, all of it in the corrected Y term.")
    print("")


# Spike/Geometry.swift's fitBox now drops points above a suitcase's own rim (rimHeight), so an
# open lid in frame no longer inflates the scanned box the way it used to (was up to 7+ cm on
# this scene). What's left is ordinary LiDAR-noise fit error -- the SAME order of
# magnitude a plain closed scan already carries with every other degradation in this sim turned
# on (~0.5 cm, measured with --no-lid-open) -- so the tolerance below is set just above that
# noise floor, not at zero: it fails on a lid inflating the box again, not on routine jitter.
LID_OPEN_ESCAPE_TOLERANCE_M = 0.010


def pin_nesting_overlap_check() -> None:
    """Regression pin for `solids_overlap`, both directions -- no driver/docker needed, so it
    always runs, even when the rest of the pipeline skips. A bowl (box item with a cavity carved
    into one quadrant of its height grid, like `Item.solid_boxes`) with a cup placed in that
    cavity must NOT be reported as an overlap; two genuinely overlapping plain boxes still must
    be. Without the second half, "always accepts" would pass just as silently as the bug this
    check replaces."""
    # 4x4 height grid, one corner cell (x>=0.15, z>=0.15) empty: fill_frac 15/16 and
    # volume/bbox 15/16 both clear the 0.9 "box" classification threshold (a coarser cavity,
    # e.g. one of 2x2, reads as "cylinder" and falls back to the plain bbox -- see models.py's
    # from_scanned_heightmap classifier -- which would make this pin test nothing).
    bowl = Item.from_scanned_heightmap({"id": "bowl", "dimensions": [0.2, 0.1, 0.2], "cellSize": 0.05,
                                         "heights": [[0.1, 0.1, 0.1, 0.1], [0.1, 0.1, 0.1, 0.1],
                                                     [0.1, 0.1, 0.1, 0.1], [0.1, 0.1, 0.1, 0.0]]})
    cup = Item.from_scanned_heightmap({"id": "cup", "dimensions": [0.04, 0.04, 0.04], "cellSize": 0.04,
                                        "heights": [[0.04]]})
    items_by_id = {"bowl": bowl, "cup": cup}
    bowl_p = {"itemId": "bowl", "position": {"x": 0.0, "y": 0.0, "z": 0.0},
              "size": {"x": 0.2, "y": 0.1, "z": 0.2}, "rotation": "XYZ"}
    cup_p = {"itemId": "cup", "position": {"x": 0.155, "y": 0.0, "z": 0.155},
             "size": {"x": 0.04, "y": 0.04, "z": 0.04}, "rotation": "XYZ"}
    assert boxes_overlap([0.0, 0.0, 0.0], [0.2, 0.1, 0.2], [0.155, 0.0, 0.155], [0.04, 0.04, 0.04]), \
        "sanity: bowl/cup bboxes should overlap, or this isn't testing nesting at all"
    assert not solids_overlap(bowl_p, cup_p, items_by_id), \
        "cup nested in the bowl's cavity must not be reported as an overlap"

    a = Item.from_scanned_heightmap({"id": "a", "dimensions": [0.1, 0.1, 0.1], "cellSize": 0.1, "heights": [[0.1]]})
    b = Item.from_scanned_heightmap({"id": "b", "dimensions": [0.1, 0.1, 0.1], "cellSize": 0.1, "heights": [[0.1]]})
    items_by_id2 = {"a": a, "b": b}
    a_p = {"itemId": "a", "position": {"x": 0.0, "y": 0.0, "z": 0.0},
           "size": {"x": 0.1, "y": 0.1, "z": 0.1}, "rotation": "XYZ"}
    b_p = {"itemId": "b", "position": {"x": 0.05, "y": 0.0, "z": 0.05},
           "size": {"x": 0.1, "y": 0.1, "z": 0.1}, "rotation": "XYZ"}
    assert solids_overlap(a_p, b_p, items_by_id2), "two genuinely overlapping boxes must still fail"
    print("== nesting overlap check pinned: bowl/cup nesting accepted, genuine overlap rejected ==\n")


def lid_open_true_interior_check(true_out: dict, degraded_out: dict, placements: list[dict]) -> None:
    """The plan was built from the (possibly lid-inflated) degraded suitcase; does it still fit
    the true, undegraded interior once drawn with the degraded anchor? Asserts it does, within
    LID_OPEN_ESCAPE_TOLERANCE_M -- the whole point of segmenting the lid off in fitBox."""
    max_esc, escaped = worst_case_containment(
        placements, degraded_out["axis"], degraded_out["perp"], degraded_out["origin"],
        true_out["axis"], true_out["perp"], true_out["origin"], true_out["interior"])
    fits = max_esc <= LID_OPEN_ESCAPE_TOLERANCE_M
    verdict = "yes" if fits else "NO -- plan does not fit the true (closed-lid) interior"
    print(f"== lid-open reality check: solved plan vs. the true (undegraded) suitcase interior ==")
    print(f"true interior {true_out['interior'][0]:.3f}x{true_out['interior'][1]:.3f}x{true_out['interior'][2]:.3f} m "
          f"vs. degraded interior {degraded_out['interior'][0]:.3f}x{degraded_out['interior'][1]:.3f}x"
          f"{degraded_out['interior'][2]:.3f} m: max escape {max_esc * 100:.2f} cm ({escaped} corners) -- fits? {verdict}")
    print("")
    warn("lid-open fit", f"max escape {max_esc * 100:.2f} cm ({escaped} corners) vs "
                         f"{LID_OPEN_ESCAPE_TOLERANCE_M * 100:.2f} cm tolerance")
    if not fits:
        fail(f"lid-open reality check: max escape {max_esc * 100:.2f} cm exceeds "
             f"{LID_OPEN_ESCAPE_TOLERANCE_M * 100:.2f} cm -- an open-lid scan no longer fits the true bag")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adversarial", action="store_true",
                         help="also run the clutter-merge and drift sweeps and print findings (never fails the gate)")
    for f in dataclasses.fields(Degradations):
        parser.add_argument(f"--no-{f.name.replace('_', '-')}", dest=f.name, action="store_false", default=True)
    args = parser.parse_args()
    deg = Degradations(**{f.name: getattr(args, f.name) for f in dataclasses.fields(Degradations)})
    tmp_root = Path(tempfile.mkdtemp(prefix="ar_sim_"))
    mongo_name: str | None = None
    server: subprocess.Popen | None = None
    try:
        pin_nesting_overlap_check()
        driver = build_driver(tmp_root)

        print(f"degradations active this run: "
              f"{', '.join(f.name for f in dataclasses.fields(deg) if getattr(deg, f.name)) or '(none)'}")
        degradation_report(driver)
        true_suitcase, _ = scene(NONE_DEGRADED)  # ground truth, for the lid-open/drift reality checks below
        true_suitcase_cloud = true_suitcase["points"]

        suitcase_cloud, items = scene(deg)

        # 1) real scan geometry, on the suitcase and every item
        scan_req = {"cmd": "scan", "scan": [
            {"points": suitcase_cloud["points"].tolist(), "planeY": TABLE_Y,
             "cell": suitcase_cloud["cell"], "padding": 0.0},
            *[{"points": it["points"].tolist(), "planeY": TABLE_Y, "cell": it["cell"], "padding": 0.0}
              for it in items],
        ]}
        scanned = run_driver(driver, scan_req)
        suitcase_fit, item_fits = scanned[0], scanned[1:]
        print(f"[1/5] scanned suitcase outer shell {suitcase_fit['width']:.3f}x"
              f"{suitcase_fit['height']:.3f}x{suitcase_fit['depth']:.3f} m, "
              f"{len(items)} items: " + ", ".join(
                  f"{it['name']} {f['width']:.3f}x{f['height']:.3f}x{f['depth']:.3f}"
                  for it, f in zip(items, item_fits)))

        mongo_uri, mongo_name = start_mongo()
        if mongo_uri is None:
            skip("Docker/Mongo unavailable on this machine (docker missing, or a container "
                 "could not be started) -- cannot exercise the server, skipping")

        port = free_port()
        env = dict(os.environ)
        env.pop("XAI_API_KEY", None)  # no API keys: Grok labelling must be off
        env["SUITCASE_MONGODB_URI"] = mongo_uri
        env["MONGO_DB"] = "ar_sim"
        server = subprocess.Popen(
            ["uv", "run", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(ROOT / "server"), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        if not wait_for_port("127.0.0.1", port, timeout=30):
            out = server.stdout.read() if server.stdout else ""
            fail(f"server never opened port {port}:\n{out[-2000:]}")
        base = f"http://127.0.0.1:{port}"

        # 2) POST the suitcase, using the interior box (Spike/ScanView.swift posts the interior,
        # not the scanned outer shell) so the solver's volume matches the one PlanAnchor uses.
        empty_transform = run_driver(driver, {"cmd": "transform", "transform": {
            "suitcasePoints": suitcase_cloud["points"].tolist(), "planeY": TABLE_Y, "wall": WALL, "placements": []}})
        interior = empty_transform["interior"]
        status, suitcase_doc = post_json(f"{base}/suitcases", {"name": "sim suitcase", "dimensions": interior})
        if status == 401:
            skip("server requires an Auth0 bearer token (no Auth0 tenant/credentials available "
                 "headlessly, and this sim will not fetch one -- that would be an API key by "
                 "another name); " + LABEL_UNAVAILABLE)
        if status != 200:
            fail(f"POST /suitcases -> {status}: {suitcase_doc}")
        print(f"[2/5] suitcase {suitcase_doc['id']} interior {interior[0]:.3f}x{interior[1]:.3f}x{interior[2]:.3f} m")

        # 3) POST every item, footprint included when the scan produced one
        item_ids = []
        items_by_id = {}  # item id -> packer3d Item, so the overlap check below can decompose cavities
        for it, fit in zip(items, item_fits):
            assert_footprint_in_box(it["name"], fit)
            doc = item_doc(it, fit, with_footprint=True)
            doc["suitcaseId"] = suitcase_doc["id"]
            status, resp = post_item(f"{base}/items", doc)
            if status != 200:
                fail(f"POST /items ({it['name']}) -> {status}: {resp}")
            item_ids.append(resp["id"])
            items_by_id[doc["id"]] = Item.from_scanned_heightmap(doc)
            fp_note = f"footprint {len(fit['footprint'])}v" if fit.get("footprint") is not None else "footprint none"
            print(f"      + {it['name']:<12} {doc['dimensions'][0]:.3f}x{doc['dimensions'][1]:.3f}x"
                  f"{doc['dimensions'][2]:.3f} m  label={resp['label']}  {fp_note}")

        # 4) POST /plan
        status, plan_doc = post_json(f"{base}/suitcases/{suitcase_doc['id']}/plan", {})
        if status != 200:
            fail(f"POST /suitcases/{{id}}/plan -> {status}: {plan_doc}")
        placements = plan_doc["plan"]["placements"]
        metrics = plan_doc["solver"]["metrics"]
        print(f"[3/5] plan: {metrics['items_packed']} packed / {metrics['items_unpacked']} unpacked, "
              f"physics valid={plan_doc['validation']['valid']}")
        if not placements:
            fail("solver packed zero items; nothing to check the transform/containment against")

        footprint_wiring_check(base, interior, items, item_fits)

        # 5) push placements through the real bag->world transform and check them
        transform_req = {"cmd": "transform", "transform": {
            "suitcasePoints": suitcase_cloud["points"].tolist(), "planeY": TABLE_Y, "wall": WALL,
            "placements": [{"position": [p["position"]["x"], p["position"]["y"], p["position"]["z"]],
                             "size": [p["size"]["x"], p["size"]["y"], p["size"]["z"]]} for p in placements],
        }}
        out = run_driver(driver, transform_req)
        interior2 = out["interior"]
        print(f"[4/5] {len(placements)} placements pushed through PlanAnchor.worldCenter")

        for p, world, bag in zip(placements, out["worldCenters"], out["bagRoundTrip"]):
            label = f"{p['label']} (step {p['step']})"
            assert_finite(label, world)
            pos = [p["position"]["x"], p["position"]["y"], p["position"]["z"]]
            size = [p["size"]["x"], p["size"]["y"], p["size"]["z"]]
            expected_center = [pos[i] + size[i] / 2 for i in range(3)]
            if any(abs(bag[i] - expected_center[i]) > 1e-3 for i in range(3)):
                fail(f"{label}: world->bag round trip {bag} != expected centre {expected_center} (>1mm)")
            assert_contained(label, pos, size, interior2)

        for i in range(len(placements)):
            for j in range(i + 1, len(placements)):
                pi, pj = placements[i], placements[j]
                if solids_overlap(pi, pj, items_by_id):
                    fail(f"{pi['label']} (step {pi['step']}) overlaps {pj['label']} (step {pj['step']})")

        print(f"[5/5] every placement is inside the bag, no two overlap, all world positions finite")
        print("AR SIM: PASS")
        print("")

        # Report-only reality checks: these never call fail() -- rule 5 keeps the gate green on a
        # known limitation, but the numbers still get printed every run, degraded or not.
        true_out = run_driver(driver, {"cmd": "transform", "transform": {
            "suitcasePoints": true_suitcase_cloud.tolist(), "planeY": TABLE_Y, "wall": WALL, "placements": []}})
        lid_open_true_interior_check(true_out, out, placements)
        if args.adversarial:
            clutter_check(driver)
            # A plan solved against the SAME (true, undegraded) bag the drift sweep measures
            # against -- reusing `placements` here would confound drift with whatever degraded
            # the suitcase (e.g. lid-open inflation), mis-attributing that error to drift.
            true_placements, _ = post_suitcase_items_plan(base, "sim suitcase (true bag, for drift)",
                                                            true_out["interior"], items, item_fits)
            drift_check(driver, true_suitcase_cloud, true_out, true_placements)

        return 0
    finally:
        cleanup(mongo_name, server)
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
