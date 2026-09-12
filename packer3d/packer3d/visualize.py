"""Debug render: python -m packer3d.visualize result.json out.png [naive|optimized]"""
from __future__ import annotations

import json
import sys

import numpy as np

ELEV = 28.0
PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948",
           "#b07aa1", "#ff9da7", "#9c755f", "#a0cbe8", "#8cd17d", "#d4a6c8"]
EDGE, FRAGILE_EDGE, PANE, OBSTACLE = (0.12, 0.12, 0.12, 1.0), (0.85, 0.1, 0.1, 1.0), (0.955, 0.955, 0.95), (0.42, 0.42, 0.44)


def _box(lo, d):
    lo = np.asarray(lo, float)
    hi = lo + np.asarray(d, float)
    c = np.array([[lo[0], lo[1], lo[2]], [hi[0], lo[1], lo[2]], [hi[0], hi[1], lo[2]], [lo[0], hi[1], lo[2]],
                  [lo[0], lo[1], hi[2]], [hi[0], lo[1], hi[2]], [hi[0], hi[1], hi[2]], [lo[0], hi[1], hi[2]]])
    return [c[i] for i in ([0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [0, 4, 7, 3])]


def _cylinder(center, axis, r, h, n=24):
    """n side quads (outward-wound) followed by the +axis cap and the -axis cap."""
    a = np.eye(3)["xyz".index(axis)]
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    ring = np.outer(np.cos(t), np.roll(a, 1)) + np.outer(np.sin(t), np.roll(a, 2))
    lo = np.asarray(center, float) - a * h / 2 + r * ring
    hi = lo + a * h
    sides = [np.array([lo[k], lo[(k + 1) % n], hi[(k + 1) % n], hi[k]]) for k in range(n)]
    return sides + [hi, lo[::-1]]


def _normals(polys):
    n = np.array([np.cross(p, np.roll(p, -1, axis=0)).sum(0) for p in polys])
    return n / np.linalg.norm(n, axis=1, keepdims=True)


def _container(c, w):
    """Back faces of the container (light panes), their outline (drawn behind) and the front edges (drawn in front)."""
    L, W, H = c["dims"]
    if c["shape"] != "cylinder":
        faces = _box((0, 0, 0), (L, W, H))
        front = _normals(faces) @ w > 0
        edges = lambda sel: [(f[k], f[(k + 1) % 4]) for f, v in zip(faces, front) if v == sel for k in range(4)]
        return [f for f, v in zip(faces, front) if not v], edges(False), edges(True)
    R = L / 2
    faces = _cylinder((R, R, H / 2), "z", R, H, n=48)
    sides = faces[:-2]
    front = _normals(sides) @ w > 0
    rims = lambda sel: [(f[0], f[1]) for f, v in zip(sides, front) if v == sel] + \
                       [(f[3], f[2]) for f, v in zip(sides, front) if v == sel]
    silhouette = [(f[0], f[3]) for k, f in enumerate(sides) if front[k] != front[k - 1]]
    return [f for f, v in zip(sides, front) if not v] + [faces[-1]], rims(False), rims(True) + silhouette


def render(result: dict, out_path: str, title: str = "") -> None:
    from matplotlib.colors import to_rgb
    from matplotlib.figure import Figure
    from matplotlib.patches import Patch
    from matplotlib.path import Path
    from matplotlib.patheffects import withStroke
    from mpl_toolkits.mplot3d.art3d import Line3DCollection, Poly3DCollection

    c = result["container"]
    L, W, H = c["dims"]
    azim = -60.0 if L >= W else -30.0
    e, a = np.radians(ELEV), np.radians(azim)
    w = np.array([np.cos(e) * np.cos(a), np.cos(e) * np.sin(a), np.sin(e)])  # towards the eye
    u = np.cross([0, 0, 1], w)
    u /= np.linalg.norm(u)
    up = np.cross(w, u)
    light = w + [0, 0, 1.2]
    light /= np.linalg.norm(light)

    # Gather every face: (polygon, owner index, base colour, edge colour, linewidth).
    polys, owner, base, edge, lw = [], [], [], [], []
    labels = []  # per owner: text
    for ob in c.get("obstacles", []):
        f = _box(ob["position"], ob["dims"])
        polys += f; owner += [len(labels)] * 6; base += [OBSTACLE] * 6; edge += [EDGE] * 6; lw += [0.5] * 6
        labels.append(ob.get("id", "obstacle"))
    for i, p in enumerate(result["placements"]):
        col = to_rgb(PALETTE[i % len(PALETTE)])
        ec, elw = (FRAGILE_EDGE, 1.1) if p.get("fragile") else (EDGE, 0.5)
        if p["shape"] == "cylinder":
            f = _cylinder(p["center"], p["axis"], p["radius"], p["height"])
            polys += f; base += [col] * len(f)
            edge += [None] * (len(f) - 2) + [ec] * 2; lw += [0.3] * (len(f) - 2) + [elw] * 2  # None: seam-hiding
        else:
            f = _box(p["position"], p["dims"])
            polys += f; base += [col] * 6; edge += [ec] * 6; lw += [elw] * 6
        owner += [len(labels)] * len(f)
        labels.append(p["item_id"])

    fig = Figure(figsize=(9, 6.5))
    ax = fig.add_axes([0.01, 0.0, 0.98, 0.94], projection="3d", proj_type="ortho")
    ax.view_init(elev=ELEV, azim=azim)
    ax.set_xlim(0, L); ax.set_ylim(0, W); ax.set_zlim(0, H)
    ax.set_box_aspect((L, W, H), zoom=1.25)
    ax.set_axis_off()

    panes, back_lines, front_lines = _container(c, w)
    for art, zpos in ((Poly3DCollection(panes, facecolors=PANE, edgecolors=PANE, linewidths=0.5), -1e3),
                      (Line3DCollection(back_lines, colors=(0.78, 0.78, 0.78), linewidths=0.7), -999.0),
                      (Line3DCollection(front_lines, colors=(0.35, 0.35, 0.35), linewidths=0.8), 1e3)):
        art.set_sort_zpos(zpos)  # panes and their outline always behind everything, front edges always in front
        ax.add_collection3d(art)

    if polys:
        nrm = _normals(polys)
        vis = nrm @ w > 0  # back-face culling: convex items, so this is exact
        polys = [p for p, v in zip(polys, vis) if v]
        nrm, owner = nrm[vis], np.array(owner)[vis]
        shade = 0.55 + 0.45 * np.clip(nrm @ light, 0, 1)
        face = np.array([base[k] for k in np.flatnonzero(vis)]) * shade[:, None]
        edges = np.array([(e if e is not None else (*f, 1.0)) for e, f in zip((edge[k] for k in np.flatnonzero(vis)), face)])
        ax.add_collection3d(Poly3DCollection(polys, facecolors=face, edgecolors=edges, linewidths=np.array(lw)[vis]))

        # Label anchor: the largest on-screen face of each item whose centre is not hidden by a nearer face.
        cen = np.array([p.mean(0) for p in polys])
        scr = [p @ np.column_stack([u, up]) for p in polys]
        area = np.array([abs(s[:, 0] @ np.roll(s[:, 1], -1) - s[:, 1] @ np.roll(s[:, 0], -1)) for s in scr])
        c2, depth = cen @ np.column_stack([u, up]), cen @ w
        covered = np.zeros(len(polys), bool)
        for j, s in enumerate(scr):
            inside = Path(s).contains_points(c2)
            if inside.any():
                dplane = (polys[j][0] @ nrm[j] - c2 @ [u @ nrm[j], up @ nrm[j]]) / (w @ nrm[j])
                covered |= inside & (owner != owner[j]) & (dplane > depth + 1e-9)
        halo = [withStroke(linewidth=2.5, foreground="white")]
        for k in range(len(labels)):
            cand = np.flatnonzero((owner == k) & ~covered)
            if len(cand):
                x, y, z = cen[cand[np.argmax(area[cand])]]
                ax.text(x, y, z, labels[k], fontsize=7, ha="center", va="center", color=(0.1, 0.1, 0.1),
                        zorder=1000, path_effects=halo)

    off = 0.04 * max(L, W, H)
    dim_style = dict(fontsize=7.5, color=(0.35, 0.35, 0.35), ha="center", va="center", zorder=1000)
    if c["shape"] == "cylinder":
        R, d = L / 2, w[:2] / np.linalg.norm(w[:2])
        ax.text(R + (R + off) * d[0], R + (R + off) * d[1], -off, f"⌀ {L:g} m", **dim_style)
        s = np.array([-d[1], d[0]])  # silhouette direction; pick the on-screen right one
        s = s if (s @ u[:2]) > 0 else -s
        ax.text(R + (R + off) * s[0], R + (R + off) * s[1], H / 2, f"{H:g} m", **dim_style)
    else:
        ax.text(L / 2, -off, -off, f"{L:g} m", **dim_style)
        ax.text(L + off, W / 2, -off, f"{W:g} m", **dim_style)
        ax.text(-off, -off, H / 2, f"{H:g} m", **dict(dim_style, ha="right"))

    m = result.get("metrics", {})
    handles = []
    if "com" in m:
        handles.append(ax.plot(*[[v] for v in m["com"]], "x", color="#d62728", ms=9, mew=2.2, zorder=1000, label="CoM")[0])
        handles.append(ax.plot(*[[v] for v in m["com_target"]], "o", mfc="none", mec="#2ca02c", ms=9, mew=2,
                               zorder=1000, label="target")[0])
    if any(p.get("fragile") for p in result["placements"]):
        handles.append(Patch(facecolor="white", edgecolor=FRAGILE_EDGE, linewidth=1.2, label="fragile"))
    if handles:
        ax.legend(handles=handles, loc="upper left", fontsize=8, frameon=False)
    sub = (f"{m.get('items_packed', '?')} packed, util {m.get('volume_utilization', 0):.1%}, "
           f"CoM lateral {m.get('com_lateral_offset', 0) * 100:.1f} cm") if m else ""
    ax.set_title(f"{title}  —  {sub}" if title and sub else title or sub, fontsize=11, pad=4)
    fig.savefig(out_path, dpi=150, facecolor="white")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print(__doc__)
        return 2
    with open(argv[0]) as fh:
        data = json.load(fh)
    key = argv[2] if len(argv) > 2 else None
    if key and key in data:
        result = data[key]
    elif "placements" in data:
        result = data
    elif "optimized" in data:
        result, key = data["optimized"], "optimized"
    else:
        raise SystemExit(f"no result named {key!r} in {argv[0]} (keys: {list(data)})")
    render(result, argv[1], key or result.get("strategy", ""))
    print(f"wrote {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
