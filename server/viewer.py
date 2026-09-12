"""Render a scanned item's coloured voxel grid or mesh, for eyeballing a scan.

Usage: python viewer.py [item_id] [out.png]
       python viewer.py --interactive [item_id]   # mouse-orbit window, every angle, no file written
       python viewer.py --demo [--interactive]    # synthetic coloured box, no Mongo needed

Defaults to the most recently scanned item that has voxels, and to viewer_out.png.
"""
from __future__ import annotations

import os
import sys

import matplotlib
if "--interactive" not in sys.argv and "-i" not in sys.argv:
    matplotlib.use("Agg")  # headless: only needed when saving straight to a file
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from pymongo import MongoClient


def demo_item() -> dict:
    """A synthetic coloured box in the same shape as a real MeshPayload (see
    Spike/ColoredMesh.swift), for trying the viewer without a phone scan or Mongo."""
    lo, hi = np.array([-0.05, -0.03, -0.02]), np.array([0.05, 0.03, 0.02])
    corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
    # 6 faces, 2 triangles each, indexed into `corners` (binary-coded: bit0=x, bit1=y, bit2=z).
    idx = lambda x, y, z: (x << 2) | (y << 1) | z  # noqa: E731
    faces = [
        (idx(0, 0, 0), idx(1, 0, 0), idx(1, 1, 0)), (idx(0, 0, 0), idx(1, 1, 0), idx(0, 1, 0)),  # z-
        (idx(0, 0, 1), idx(1, 1, 1), idx(1, 0, 1)), (idx(0, 0, 1), idx(0, 1, 1), idx(1, 1, 1)),  # z+
        (idx(0, 0, 0), idx(0, 1, 0), idx(0, 1, 1)), (idx(0, 0, 0), idx(0, 1, 1), idx(0, 0, 1)),  # x-
        (idx(1, 0, 0), idx(1, 1, 1), idx(1, 1, 0)), (idx(1, 0, 0), idx(1, 0, 1), idx(1, 1, 1)),  # x+
        (idx(0, 0, 0), idx(1, 0, 1), idx(1, 0, 0)), (idx(0, 0, 0), idx(0, 0, 1), idx(1, 0, 1)),  # y-
        (idx(0, 1, 0), idx(1, 1, 0), idx(1, 1, 1)), (idx(0, 1, 0), idx(1, 1, 1), idx(0, 1, 1)),  # y+
    ]
    face_colour = [(230, 60, 60), (230, 60, 60), (60, 200, 90), (60, 200, 90), (60, 120, 230), (60, 120, 230),
                   (230, 200, 60), (230, 200, 60), (200, 60, 200), (200, 60, 200), (60, 200, 200), (60, 200, 200)]
    vertices, colours = [], []
    for face, colour in zip(faces, face_colour):
        for v in face:
            vertices += corners[v].tolist()
            colours += list(colour)
    mesh = {"vertices": vertices, "colours": colours, "triangleCount": len(faces)}
    return {"_id": "demo-box", "label": "demo box", "mesh": mesh, "viewCoverage": 1.0}


def load_item(db, item_id: str | None) -> dict:
    query = {"_id": item_id} if item_id else {"voxels": {"$ne": None}}
    doc = db.items.find_one(query, sort=[("createdAt", -1)])
    if doc is None:
        raise SystemExit(f"no item found for {query!r}")
    if not doc.get("voxels"):
        raise SystemExit(f"item {doc['_id']} has no voxels — scan it with the walk-around capture, not the old single-tap one")
    return doc


def points_and_colours(voxels: dict) -> tuple[np.ndarray, np.ndarray]:
    """VoxelPayload (see Spike/Voxels.swift) -> (Nx3 world points, Nx3 RGB in 0..1)."""
    origin = np.array(voxels["origin"], dtype=float)
    size = float(voxels["voxelSize"])
    idx = np.array(voxels["indices"], dtype=float).reshape(-1, 3)
    points = origin + idx * size

    colours = voxels.get("colours") or []
    if colours:
        rgb = np.array(colours, dtype=float).reshape(-1, 3) / 255.0
    else:
        rgb = np.full((len(points), 3), 0.5)  # no colour recorded (behind camera / poor lighting)
    return points, rgb


def triangles_and_colours(mesh: dict) -> tuple[np.ndarray, np.ndarray]:
    """MeshPayload (see Spike/ColoredMesh.swift) -> (Nx3x3 triangles, Nx3 face RGB in 0..1).

    Vertices are already per-triangle (no shared buffer, see ColoredMesh.swift), so this
    just reshapes; the face colour is the mean of its three vertex colours, since a
    matplotlib Poly3DCollection face takes one colour, not a per-vertex gradient.
    """
    verts = np.array(mesh["vertices"], dtype=float).reshape(-1, 3, 3)
    rgb = np.array(mesh["colours"], dtype=float).reshape(-1, 3, 3) / 255.0
    return verts, rgb.mean(axis=1)


def _bounds(points: np.ndarray) -> tuple[np.ndarray, float]:
    lo, hi = points.min(0), points.max(0)
    return (lo + hi) / 2, max((hi - lo).max() / 2, 0.01)


def _finish(fig, out_path: str | None) -> None:
    """Either open a mouse-orbit window (every angle, live) or save one angle to disk."""
    if out_path is None:
        plt.show()  # mplot3d's default navigation already does click-drag orbit + scroll zoom
    else:
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
        print(f"wrote {out_path}")


def render_mesh(triangles: np.ndarray, face_colours: np.ndarray, out_path: str | None, title: str) -> None:
    """Filled, solid-looking render: the actual scanned surface, not a point cloud."""
    # Team frame is x=right, y=up, z=forward; matplotlib's 3rd axis is drawn vertical,
    # so plot (x, z, y) to keep "up" pointing up on screen.
    swapped = triangles[:, :, [0, 2, 1]]
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111, projection="3d")
    poly = Poly3DCollection(swapped, facecolors=face_colours, edgecolors="none")
    ax.add_collection3d(poly)
    centre, half = _bounds(triangles.reshape(-1, 3))
    ax.set_xlim(centre[0] - half, centre[0] + half)
    ax.set_ylim(centre[2] - half, centre[2] + half)
    ax.set_zlim(centre[1] - half, centre[1] + half)
    ax.set_xlabel("x (right)"); ax.set_ylabel("z (forward)"); ax.set_zlabel("y (up)")
    ax.set_box_aspect((1, 1, 1))
    ax.set_title(title)
    _finish(fig, out_path)


def render_points(points: np.ndarray, colours: np.ndarray, out_path: str | None, title: str) -> None:
    """Fallback for scans made before mesh capture existed: sparse point cloud."""
    fig = plt.figure(figsize=(8, 8))
    ax = fig.add_subplot(111, projection="3d")
    ax.scatter(points[:, 0], points[:, 2], points[:, 1], c=colours, s=4, depthshade=False)
    ax.set_xlabel("x (right)"); ax.set_ylabel("z (forward)"); ax.set_zlabel("y (up)")
    centre, half = _bounds(points)
    ax.set_xlim(centre[0] - half, centre[0] + half)
    ax.set_ylim(centre[2] - half, centre[2] + half)
    ax.set_zlim(centre[1] - half, centre[1] + half)
    ax.set_box_aspect((1, 1, 1))
    ax.set_title(title + " (point cloud — no mesh in this scan)")
    _finish(fig, out_path)


if __name__ == "__main__":
    flags = {"--demo", "--interactive", "-i"}
    interactive = "--interactive" in sys.argv or "-i" in sys.argv
    demo = "--demo" in sys.argv
    positional = [a for a in sys.argv[1:] if a not in flags]
    item_id = None if demo else (positional[0] if len(positional) > 0 else None)
    default_out = positional[0] if demo and positional else (positional[1] if len(positional) > 1 else "viewer_out.png")
    out_path = None if interactive else default_out

    if demo:
        doc = demo_item()
    else:
        db = MongoClient(os.environ.get("SUITCASE_MONGODB_URI", "mongodb://localhost:27017"),
                          serverSelectionTimeoutMS=8000)[os.environ.get("MONGO_DB", "suitcase")]
        doc = load_item(db, item_id)
    coverage = doc.get("viewCoverage")
    covered = f", {coverage:.0%} covered" if coverage else ""

    if doc.get("mesh") and doc["mesh"].get("triangleCount"):
        triangles, face_colours = triangles_and_colours(doc["mesh"])
        title = f"{doc.get('label') or doc['_id']} — {len(triangles)} triangles{covered}"
        render_mesh(triangles, face_colours, out_path, title)
    else:
        points, colours = points_and_colours(doc["voxels"])
        title = f"{doc.get('label') or doc['_id']} — {len(points)} voxels{covered}"
        render_points(points, colours, out_path, title)
