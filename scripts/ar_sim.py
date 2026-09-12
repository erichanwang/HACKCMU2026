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
# tests/swift/drift/main.swift's own mid/worst-realistic combos (planeY cm, axis deg, drift cm)
DRIFT_MID = (1.0, 2.0, 2.0)
DRIFT_WORST = (3.0, 5.0, 5.0)


def skip(message: str) -> None:
    print(f"AR SIM: SKIP -- {message}")
    raise SystemExit(0)


def fail(message: str) -> None:
    print(f"AR SIM: FAIL -- {message}", file=sys.stderr)
    raise SystemExit(1)


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
struct ScanOut: Codable { let width: Float; let height: Float; let depth: Float; let axis: [Float]; let heights: [[Float]] }

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
                        axis: [fit.axis.x, fit.axis.y, fit.axis.z], heights: hm)
    }
}

func runTransform(_ t: TransformIn) -> TransformOut {
    let pts = t.suitcasePoints.map { SIMD3<Float>($0[0], $0[1], $0[2]) }
    guard let outer = fitBox(points: pts, planeY: t.planeY, padding: 0) else {
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
    for phase in (0.0, cell / 2):
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
    print("")


def drift_check(driver: Path, true_suitcase_points: np.ndarray, true_out: dict, placements: list[dict]) -> None:
    """World-origin drift + plane error between scan time and overlay time, at the mid/worst
    magnitudes tests/swift/drift/main.swift itself characterised (median max-corner error there:
    4.84cm over the full realistic Monte Carlo range). Rotate+translate the SAME suitcase cloud
    the real fitBox already fit, re-fit it, and check the SAME solved plan against the true bag."""
    pivot = np.array([SUITCASE_ARGS["cx"], SUITCASE_ARGS["cz"]])
    print("== drift: world-origin drift + plane/axis error between scan and overlay ==")
    print(f"{'scenario':<45} {'max corner err':<16} {'max escape from true bag':<26} fits?")
    for label, (dplaney_cm, daxis_deg, ddrift_cm) in (("mid-range", DRIFT_MID), ("worst realistic", DRIFT_WORST)):
        theta = math.radians(daxis_deg)
        xz = true_suitcase_points[:, [0, 2]] - pivot
        xz = xz @ _rot(theta).T + pivot + np.array([1, 1]) / math.sqrt(2) * (ddrift_cm / 100)
        drifted_points = true_suitcase_points.copy()
        drifted_points[:, 0], drifted_points[:, 2] = xz[:, 0], xz[:, 1]
        drifted_out = run_driver(driver, {"cmd": "transform", "transform": {
            "suitcasePoints": drifted_points.tolist(), "planeY": TABLE_Y + dplaney_cm / 100, "wall": WALL,
            "placements": [{"position": [p["position"]["x"], p["position"]["y"], p["position"]["z"]],
                             "size": [p["size"]["x"], p["size"]["y"], p["size"]["z"]]} for p in placements]}})
        max_corner_err = 0.0
        for p in placements:
            pos = [p["position"]["x"], p["position"]["y"], p["position"]["z"]]
            size = [p["size"]["x"], p["size"]["y"], p["size"]["z"]]
            true_c = placement_corners(true_out["axis"], true_out["perp"], true_out["origin"], pos, size)
            drift_c = placement_corners(drifted_out["axis"], drifted_out["perp"], drifted_out["origin"], pos, size)
            max_corner_err = max(max_corner_err, max(float(np.linalg.norm(dc - tc)) for tc, dc in zip(true_c, drift_c)))
        max_esc, escaped = worst_case_containment(
            placements, drifted_out["axis"], drifted_out["perp"], drifted_out["origin"],
            true_out["axis"], true_out["perp"], true_out["origin"], true_out["interior"])
        verdict = "NO -- overlay escapes the real bag" if max_esc > 1e-4 else "yes"
        print(f"{label + f' ({dplaney_cm:.0f}cm plane, {daxis_deg:.0f}deg axis, {ddrift_cm:.0f}cm drift)':<45} "
              f"{max_corner_err * 100:6.2f} cm       {max_esc * 100:6.2f} cm ({escaped} corners)      {verdict}")
    print("")


def lid_open_true_interior_check(true_out: dict, degraded_out: dict, placements: list[dict]) -> None:
    """The plan was built from the (possibly lid-inflated) degraded suitcase; does it still fit
    the true, undegraded interior once drawn with the degraded anchor?"""
    max_esc, escaped = worst_case_containment(
        placements, degraded_out["axis"], degraded_out["perp"], degraded_out["origin"],
        true_out["axis"], true_out["perp"], true_out["origin"], true_out["interior"])
    verdict = "NO -- plan does not fit the true (closed-lid) interior" if max_esc > 1e-4 else "yes"
    print(f"== lid-open reality check: solved plan vs. the true (undegraded) suitcase interior ==")
    print(f"true interior {true_out['interior'][0]:.3f}x{true_out['interior'][1]:.3f}x{true_out['interior'][2]:.3f} m "
          f"vs. degraded interior {degraded_out['interior'][0]:.3f}x{degraded_out['interior'][1]:.3f}x"
          f"{degraded_out['interior'][2]:.3f} m: max escape {max_esc * 100:.2f} cm ({escaped} corners) -- fits? {verdict}")
    print("")


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

        # 3) POST every item
        item_ids = []
        for it, fit in zip(items, item_fits):
            doc = {"id": f"{it['name']}-{uuid.uuid4().hex[:8]}", "suitcaseId": suitcase_doc["id"],
                   "dimensions": [fit["width"], fit["height"], fit["depth"]], "cellSize": it["cell"],
                   "heights": fit["heights"]}
            status, resp = post_item(f"{base}/items", doc)
            if status != 200:
                fail(f"POST /items ({it['name']}) -> {status}: {resp}")
            item_ids.append(resp["id"])
            print(f"      + {it['name']:<12} {doc['dimensions'][0]:.3f}x{doc['dimensions'][1]:.3f}x"
                  f"{doc['dimensions'][2]:.3f} m  label={resp['label']}")

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
                pos_i = [pi["position"]["x"], pi["position"]["y"], pi["position"]["z"]]
                size_i = [pi["size"]["x"], pi["size"]["y"], pi["size"]["z"]]
                pos_j = [pj["position"]["x"], pj["position"]["y"], pj["position"]["z"]]
                size_j = [pj["size"]["x"], pj["size"]["y"], pj["size"]["z"]]
                if boxes_overlap(pos_i, size_i, pos_j, size_j):
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
            drift_check(driver, true_suitcase_cloud, true_out, placements)

        return 0
    finally:
        cleanup(mongo_name, server)
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
