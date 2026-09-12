"""Debug render: python -m packer3d.visualize result.json out.png [naive|optimized]"""
from __future__ import annotations

import json
import sys

import numpy as np


def _box_faces(lo, d):
    x, y, z = lo
    dx, dy, dz = d
    v = np.array([[x, y, z], [x + dx, y, z], [x + dx, y + dy, z], [x, y + dy, z],
                  [x, y, z + dz], [x + dx, y, z + dz], [x + dx, y + dy, z + dz], [x, y + dy, z + dz]])
    idx = [[0, 1, 2, 3], [4, 5, 6, 7], [0, 1, 5, 4], [2, 3, 7, 6], [1, 2, 6, 5], [0, 3, 7, 4]]
    return [v[i] for i in idx]


def _cylinder_surface(center, axis, radius, height, n=24):
    t = np.linspace(0, 2 * np.pi, n)
    h = np.linspace(-height / 2, height / 2, 2)
    T, Hh = np.meshgrid(t, h)
    a, b = radius * np.cos(T), radius * np.sin(T)
    cx, cy, cz = center
    if axis == "z":
        return cx + a, cy + b, cz + Hh
    if axis == "x":
        return cx + Hh, cy + a, cz + b
    return cx + a, cy + Hh, cz + b


def render(result: dict, out_path: str, title: str = "") -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    c = result["container"]
    L, W, H = c["dims"]
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    if c["shape"] == "cylinder":
        R = L / 2
        t = np.linspace(0, 2 * np.pi, 60)
        for z in (0, H):
            ax.plot(R + R * np.cos(t), R + R * np.sin(t), z, color="k", lw=0.8)
        for k in range(0, 60, 15):
            ax.plot([R + R * np.cos(t[k])] * 2, [R + R * np.sin(t[k])] * 2, [0, H], color="k", lw=0.5)
    else:
        for f in _box_faces((0, 0, 0), (L, W, H)):
            ax.add_collection3d(Poly3DCollection([f], facecolors="none", edgecolors="k", linewidths=0.8))
    for ob in c.get("obstacles", []):
        ax.add_collection3d(Poly3DCollection(_box_faces(ob["position"], ob["dims"]), facecolors="0.4",
                                             edgecolors="k", alpha=0.6))
    cmap = plt.get_cmap("tab20")
    for i, p in enumerate(result["placements"]):
        col = cmap(i % 20)
        if p["shape"] == "cylinder":
            X, Y, Z = _cylinder_surface(p["center"], p["axis"], p["radius"], p["height"])
            ax.plot_surface(X, Y, Z, color=col, alpha=0.8, linewidth=0)
        else:
            ax.add_collection3d(Poly3DCollection(_box_faces(p["position"], p["dims"]), facecolors=col,
                                                 edgecolors="k", linewidths=0.4, alpha=0.85))
        cx, cy, cz = p["center"]
        ax.text(cx, cy, cz, p["item_id"], fontsize=6, ha="center")
    m = result.get("metrics", {})
    if "com" in m:
        ax.scatter(*m["com"], color="red", s=60, marker="x", label="CoM")
        ax.scatter(*m["com_target"], color="green", s=40, marker="o", label="target")
        ax.legend(loc="upper left", fontsize=8)
    ax.set_xlim(0, L); ax.set_ylim(0, W); ax.set_zlim(0, H)
    ax.set_box_aspect((L, W, H))
    ax.set_xlabel("x"); ax.set_ylabel("y"); ax.set_zlabel("z")
    sub = (f"{m.get('items_packed', '?')} packed, util {m.get('volume_utilization', 0):.1%}, "
           f"CoM lateral {m.get('com_lateral_offset', 0) * 100:.1f} cm") if m else ""
    ax.set_title(f"{title} {sub}".strip())
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


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
