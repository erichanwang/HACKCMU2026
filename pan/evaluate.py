"""Rollout evaluator: PAN/mock rollout frames -> structured `RiskSignals`.

This is PAN.md Level 3 (§5): a vision evaluator that reads the *pixels* of a
counterfactual rollout and emits a small, honest risk record that the ranking
layer may use as ONE cost term among several (§19). It is never a gate.

What this CAN infer
-------------------
* Rendered/segmentable rollouts (observation carries `metadata["object_colors"]`):
  per-object pixel area, centroid, bounding box and how those change between the
  first and last rollout frame. From that: apparent 2D displacement of
  non-acted objects (`visible_shift`), gross silhouette change of an object
  (`possible_topple` proxy), how much of a target region is covered by other
  objects' pixels at the end (`occlusion_risk`, `accessibility_risk`).
* Unsegmented real camera rollouts: only *where and how much the image changed*.

What this CANNOT infer -- do not claim otherwise anywhere in the UI or the pitch
-------------------------------------------------------------------------------
* Physics. Nothing here recovers mass, friction, contact forces, support or
  penetration. Feasibility is decided exclusively by `physics.validator`
  (PAN.md §6: hard constraints vs learned predictions).
* Real 3D motion. All measurements are in image pixels from one viewpoint, so
  motion along the camera axis, rotation in depth, and anything hidden behind
  another object are invisible or aliased into the same numbers.
* True toppling. A flat object standing up and the renderer simply showing a
  different face of it produce the same 2D aspect/area change. `possible_topple`
  is a *silhouette-change* flag with a suggestive name, nothing more.
* Occlusion ordering / depth. "Covered" means "these pixels now belong to
  another object", which is also what "moved out of frame" looks like.
* Anything at all about object identity in the unsegmented regime: without a
  color map we cannot tell which thing moved, so `possible_topple` is forced to
  False and confidence is capped at 0.35.
* Model accuracy. These are proxies measured on a *learned* video prediction;
  errors of the world model and errors of this evaluator are not separable here
  (PAN.md §24: no fabricated quantitative accuracy).

Every number the evaluator used is copied into `RiskSignals.evidence`, and every
weak proxy states its weakness in `RiskSignals.notes`, so a human reviewing the
side-by-side (`compose_side_by_side`) can overrule it at a glance.
"""
from __future__ import annotations

from collections import deque
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pan.types import Observation, PackingAction, RiskSignals, SimulationResult

# Every tunable lives here; `evaluate_rollout(thresholds=...)` overrides per call.
DEFAULT_THRESHOLDS: dict[str, float] = {
    # --- segmentable regime ---
    "color_tol": 60.0,          # RGB Euclidean radius around a palette color (absorbs shading + mock noise)
    "min_area": 25.0,           # px; a mask smaller than this is "not confidently segmented"
    "shift_frac_diag": 0.02,    # centroid move > 2% of the image diagonal = visible shift
    "aspect_change": 0.40,      # >40% relative change of bbox w/h
    "area_change": 0.50,        # >50% relative change of visible area
    "sample_k": 5.0,            # frames sampled for the per-frame area/noise track
    "noise_median_abs_diff": 12.0,  # mean over consecutive sampled pairs of median|Δ|
    "base_confidence": 0.80,
    "confidence_min": 0.05,
    "confidence_max": 0.90,
    # --- unsegmented regime ---
    "gray_diff": 25.0,          # per-pixel |Δgrey| counted as "changed"
    "unseg_shift_frac": 0.05,   # >5% of the pixels outside the acted region changed
    "unseg_confidence": 0.30,
    "unseg_confidence_max": 0.35,
}

_GREY = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _merged(thresholds: Optional[dict]) -> dict[str, float]:
    t = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        t.update(thresholds)
    return t


def _sample_indices(n: int, k: int) -> list[int]:
    if n <= k:
        return list(range(n))
    return sorted({int(round(i)) for i in np.linspace(0, n - 1, k)})


def sample_frames(frames: list[np.ndarray], k: int = 5) -> list[np.ndarray]:
    """`k` evenly spaced frames, always including the first and the last."""
    return [frames[i] for i in _sample_indices(len(frames), k)]


# ------------------------------------------------------------------ segmentation
def segment_by_color(
    frame: np.ndarray,
    object_colors: dict[str, tuple[int, int, int]],
    tol: Optional[float] = None,
) -> dict[str, np.ndarray]:
    """Nearest-palette-color segmentation of one RGB frame.

    Each pixel is assigned to the closest color in `object_colors` (RGB Euclidean)
    when the distance is within `tol`, else to the background (no mask). `tol`
    exists because `pan/observation.py` shades faces and the world model adds
    noise, so an object's pixels are its base color *modulated*, never equal to it.
    Returns {object_id: bool mask (H, W)} for every id in the palette.
    """
    tol = DEFAULT_THRESHOLDS["color_tol"] if tol is None else float(tol)
    ids = sorted(object_colors)
    if not ids:
        return {}
    f = np.asarray(frame, dtype=np.int32)
    # (H, W, k) squared distances, one palette entry at a time (keeps peak memory at H*W*3).
    d2 = np.stack([((f - np.asarray(object_colors[i], dtype=np.int32)) ** 2).sum(-1) for i in ids], axis=-1)
    nearest = d2.argmin(-1)
    within = d2.min(-1) <= tol * tol
    return {oid: (nearest == k) & within for k, oid in enumerate(ids)}


def _stats(mask: np.ndarray, min_area: float) -> dict[str, Any]:
    area = int(mask.sum())
    out: dict[str, Any] = {"area": area, "visible": area >= min_area}
    if not out["visible"]:
        return out
    ys, xs = np.nonzero(mask)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    w, h = x1 - x0 + 1, y1 - y0 + 1
    out.update(
        centroid=[round(float(xs.mean()), 3), round(float(ys.mean()), 3)],
        bbox=[x0, y0, x1, y1],
        width=w,
        height=h,
        aspect=round(w / h, 5),
    )
    return out


def _rel(a: float, b: float) -> float:
    """Relative change from a to b, guarded against a == 0."""
    return abs(b - a) / max(abs(a), 1e-9)


def _noise(frames: list[np.ndarray]) -> float:
    """Mean over consecutive pairs of the median absolute per-pixel difference."""
    if len(frames) < 2:
        return 0.0
    diffs = [
        float(np.median(np.abs(np.asarray(a, np.int32) - np.asarray(b, np.int32))))
        for a, b in zip(frames, frames[1:])
    ]
    return round(sum(diffs) / len(diffs), 4)


# ------------------------------------------------------------------- regime A
def _evaluate_segmented(
    frames: list[np.ndarray],
    object_colors: dict[str, tuple[int, int, int]],
    acted_id: str,
    t: dict[str, float],
    expected_final: Optional[np.ndarray],
    next_target_mask: Optional[np.ndarray],
    next_object_id: Optional[str],
) -> RiskSignals:
    notes: list[str] = []
    h, w = frames[0].shape[:2]
    diag = float(np.hypot(w, h))
    shift_px = t["shift_frac_diag"] * diag
    min_area = t["min_area"]

    sampled_idx = _sample_indices(len(frames), int(t["sample_k"]))
    sampled = [frames[i] for i in sampled_idx]
    seg = [segment_by_color(f, object_colors, t["color_tol"]) for f in sampled]
    first, last = seg[0], seg[-1]

    objects: dict[str, Any] = {}
    for oid in sorted(object_colors):
        s0, s1 = _stats(first[oid], min_area), _stats(last[oid], min_area)
        rec: dict[str, Any] = {
            "first": s0,
            "last": s1,
            "area_track": [int(s[oid].sum()) for s in seg],
            "acted": oid == acted_id,
        }
        if s0["visible"] and s1["visible"]:
            d = float(np.hypot(s1["centroid"][0] - s0["centroid"][0], s1["centroid"][1] - s0["centroid"][1]))
            rec["displacement_px"] = round(d, 3)
            rec["displacement_frac_diag"] = round(d / diag, 5)
            rec["aspect_change"] = round(_rel(s0["aspect"], s1["aspect"]), 5)
            rec["area_change"] = round(_rel(s0["area"], s1["area"]), 5)
        objects[oid] = rec

    # visible_shift: a NON-acted object that stayed visible moved more than the threshold.
    shifted = [
        oid
        for oid, r in objects.items()
        if not r["acted"] and r.get("displacement_px", 0.0) > shift_px
    ]
    visible_shift = bool(shifted)

    # possible_topple: gross silhouette change of the acted object or of a shifted one.
    topple_ids, topple_evidence = [], {}
    for oid in [acted_id] + shifted:
        r = objects.get(oid)
        if not r or "aspect_change" not in r:
            continue
        flagged = r["aspect_change"] > t["aspect_change"] or r["area_change"] > t["area_change"]
        topple_evidence[oid] = {
            "aspect_first": r["first"].get("aspect"),
            "aspect_last": r["last"].get("aspect"),
            "aspect_change": r["aspect_change"],
            "area_first": r["first"]["area"],
            "area_last": r["last"]["area"],
            "area_change": r["area_change"],
            "flagged": flagged,
        }
        if flagged:
            topple_ids.append(oid)
    possible_topple = bool(topple_ids)
    if possible_topple:
        notes.append(
            "possible_topple is a 2D silhouette-change flag (aspect/area) for "
            f"{topple_ids}; a re-shaded or depth-rotated object looks identical to a real topple."
        )

    others_last = np.zeros((h, w), dtype=bool)
    for oid, m in last.items():
        if oid != acted_id:
            others_last |= m
    any_last = others_last | last.get(acted_id, np.zeros((h, w), dtype=bool))

    # occlusion_risk
    occ: dict[str, Any] = {}
    if expected_final is not None:
        exp_mask = segment_by_color(expected_final, object_colors, t["color_tol"]).get(
            acted_id, np.zeros((h, w), dtype=bool)
        )
        exp_area = int(exp_mask.sum())
        covered = int((exp_mask & others_last).sum())
        occlusion_risk = covered / exp_area if exp_area else 0.0
        occ = {"mode": "expected_final", "expected_area": exp_area, "covered_by_others_px": covered}
        if not exp_area:
            notes.append("expected_final contains no pixels of the acted object; occlusion_risk defaulted to 0.")
    else:
        a0 = objects.get(acted_id, {}).get("first", {}).get("area", 0)
        a1 = objects.get(acted_id, {}).get("last", {}).get("area", 0)
        occlusion_risk = max(0.0, 1.0 - (a1 / a0)) if a0 else 0.0
        occ = {"mode": "area_shrink_proxy", "first_area": int(a0), "last_area": int(a1)}
        notes.append(
            "occlusion_risk is the WEAK proxy (no expected_final given): the acted object's "
            "visible-area loss between first and last frame, which also fires when it simply "
            "moves out of frame or turns a smaller face to the camera."
        )

    # accessibility_risk
    acc: dict[str, Any] = {}
    if next_target_mask is not None:
        region = np.asarray(next_target_mask, dtype=bool)
        region_area = int(region.sum())
        covered = int((region & any_last).sum())
        accessibility_risk = covered / region_area if region_area else 0.0
        acc = {"mode": "next_target_mask", "region_area": region_area, "covered_px": covered}
    elif next_object_id and next_object_id in first:
        region = first[next_object_id]
        region_area = int(region.sum())
        blockers = np.zeros((h, w), dtype=bool)
        for oid, m in last.items():
            if oid != next_object_id:
                blockers |= m
        covered = int((region & blockers).sum())
        accessibility_risk = covered / region_area if region_area else 0.0
        acc = {
            "mode": "next_object_id",
            "next_object_id": next_object_id,
            "region_area": region_area,
            "covered_px": covered,
        }
        notes.append(
            f"accessibility_risk used {next_object_id}'s FIRST-frame footprint as the region to "
            "reach into; that is where it currently sits, not where a hand would come from."
        )
    else:
        accessibility_risk = 0.5 * occlusion_risk
        acc = {"mode": "occlusion_fallback", "factor": 0.5}
        notes.append(
            "no next-action target given, so accessibility_risk is a fallback of "
            "0.5 * occlusion_risk and carries no independent information."
        )

    # confidence
    conf = t["base_confidence"]
    conf_terms = {"base": t["base_confidence"]}
    if len(frames) < 3:
        conf -= 0.2
        conf_terms["few_frames"] = -0.2
    acted_track = objects.get(acted_id, {}).get("area_track", [])
    if not any(a >= min_area for a in acted_track):
        conf -= 0.2
        conf_terms["acted_object_unsegmented"] = -0.2
        notes.append(
            f"acted object {acted_id!r} was never confidently segmented (area < {min_area:g} px "
            "in every sampled frame); its signals are close to guesswork."
        )
    noise = _noise(sampled)
    if noise > t["noise_median_abs_diff"]:
        conf -= 0.1
        conf_terms["noisy_frames"] = -0.1
    confidence = float(np.clip(conf, t["confidence_min"], t["confidence_max"]))

    notes.append(
        "Visual proxies from a learned rollout, one viewpoint, image pixels only: a ranking "
        "hint, never a feasibility gate -- physics decides validity (PAN.md §6)."
    )
    return RiskSignals(
        accessibility_risk=round(float(np.clip(accessibility_risk, 0.0, 1.0)), 5),
        visible_shift=visible_shift,
        possible_topple=possible_topple,
        occlusion_risk=round(float(np.clip(occlusion_risk, 0.0, 1.0)), 5),
        confidence=round(confidence, 5),
        evidence={
            "regime": "segmented",
            "acted_object": acted_id,
            "image_size": [w, h],
            "diagonal_px": round(diag, 3),
            "shift_px_threshold": round(shift_px, 3),
            "frame_count": len(frames),
            "sampled_frame_indices": sampled_idx,
            "objects": objects,
            "shifted_objects": shifted,
            "topple": topple_evidence,
            "occlusion": occ,
            "accessibility": acc,
            "noise_median_abs_diff": noise,
            "confidence_terms": conf_terms,
            "thresholds": dict(t),
        },
        notes=notes,
    )


# ------------------------------------------------------------------- regime B
def _largest_blob_bbox(mask: np.ndarray) -> tuple[Optional[list[int]], int]:
    """bbox [x0, y0, x1, y1] and size of the largest 4-connected blob of `mask`.

    ponytail: pure-python BFS over the True pixels only (no scipy/cv2 here); fine
    for demo-resolution frames, swap in `scipy.ndimage.label` if that ever lands.
    """
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best_bbox, best_n = None, 0
    for sy, sx in zip(*np.nonzero(mask)):
        if seen[sy, sx]:
            continue
        seen[sy, sx] = True
        q = deque([(int(sy), int(sx))])
        n = 0
        x0 = x1 = int(sx)
        y0 = y1 = int(sy)
        while q:
            y, x = q.popleft()
            n += 1
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if n > best_n:
            best_n, best_bbox = n, [x0, y0, x1, y1]
    return best_bbox, best_n


def _evaluate_unsegmented(
    frames: list[np.ndarray],
    t: dict[str, float],
    target_region_px: Optional[tuple[int, int, int, int]],
) -> RiskSignals:
    notes = [
        "UNSEGMENTED regime: real RGB frames with no object color map, so nothing here is "
        "attributed to a specific object -- only image change energy. Confidence is capped "
        f"at {t['unseg_confidence_max']:g}.",
        "Visual proxies from a learned rollout: a ranking hint, never a feasibility gate "
        "-- physics decides validity (PAN.md §6).",
    ]
    g = [np.asarray(f, np.float32) @ _GREY for f in frames]
    h, w = g[0].shape
    thr = t["gray_diff"]
    changed_total = np.abs(g[-1] - g[0]) > thr

    # Acted region: caller's bbox, else the largest blob of EARLY change (frame 0 -> 1).
    if target_region_px is not None:
        x0, y0, x1, y1 = (int(v) for v in target_region_px)
        region_src = "target_region_px"
        blob_n = None
    else:
        early = np.abs(g[1] - g[0]) > thr
        bbox, blob_n = _largest_blob_bbox(early)
        region_src = "largest_early_change_blob"
        if bbox is None:
            x0, y0, x1, y1 = 0, 0, -1, -1  # empty region
        else:
            x0, y0, x1, y1 = bbox
    region = np.zeros((h, w), dtype=bool)
    if x1 >= x0 and y1 >= y0:
        region[max(0, y0) : y1 + 1, max(0, x0) : x1 + 1] = True

    outside = ~region
    outside_area = int(outside.sum())
    outside_changed = int((changed_total & outside).sum())
    outside_frac = outside_changed / outside_area if outside_area else 0.0
    visible_shift = bool(outside_frac > t["unseg_shift_frac"])

    region_area = int(region.sum())
    inside_persist = int((changed_total & region).sum())
    occlusion_risk = inside_persist / region_area if region_area else 0.0

    notes.append(
        "possible_topple forced to False: not inferable without segmentation."
    )
    notes.append(
        "occlusion_risk here is change energy inside the acted region that PERSISTS into the "
        "last frame (first-vs-last difference). It cannot distinguish 'something now covers "
        "the target' from 'the object itself moved there', and lighting change alone triggers it."
    )

    confidence = float(
        np.clip(
            t["unseg_confidence"] - (0.1 if len(frames) < 3 else 0.0),
            t["confidence_min"],
            t["unseg_confidence_max"],
        )
    )
    return RiskSignals(
        accessibility_risk=round(0.5 * float(np.clip(occlusion_risk, 0.0, 1.0)), 5),
        visible_shift=visible_shift,
        possible_topple=False,
        occlusion_risk=round(float(np.clip(occlusion_risk, 0.0, 1.0)), 5),
        confidence=round(confidence, 5),
        evidence={
            "regime": "unsegmented",
            "image_size": [w, h],
            "frame_count": len(frames),
            "acted_region_source": region_src,
            "acted_region_bbox": [x0, y0, x1, y1],
            "acted_region_blob_px": blob_n,
            "gray_diff_threshold": thr,
            "changed_px_total": int(changed_total.sum()),
            "outside_region": {
                "area": outside_area,
                "changed_px": outside_changed,
                "changed_fraction": round(outside_frac, 5),
                "threshold": t["unseg_shift_frac"],
            },
            "inside_region": {
                "area": region_area,
                "persisting_changed_px": inside_persist,
            },
            "thresholds": dict(t),
        },
        notes=notes,
    )


# ------------------------------------------------------------------ public API
def evaluate_rollout(
    result: SimulationResult,
    observation: Observation,
    action: PackingAction,
    *,
    expected_final: Optional[np.ndarray] = None,
    next_target_mask: Optional[np.ndarray] = None,
    next_object_id: Optional[str] = None,
    thresholds: Optional[dict] = None,
) -> Optional[RiskSignals]:
    """Extract `RiskSignals` from one rollout, or None when there is nothing to read.

    Returns None if `result.status != "complete"`, if fewer than 2 frames came
    back, or if the frames are not a consistent (H, W, 3) stack -- the caller
    (`pan/rollouts.py`) treats None as "no risk information", never as "safe".

    Regime is chosen from the observation: `metadata["object_colors"]` present ->
    color segmentation (confidence up to 0.8); absent -> frame-difference only
    (confidence <= 0.35). `next_target_mask` (or `next_object_id`) and
    `expected_final` are optional and strictly upgrade the signals; without them
    the weaker proxies are used and `notes` says so.

    See the module docstring for what these signals can and cannot infer.
    """
    if result.status != "complete":
        return None
    frames = list(result.frames)
    if len(frames) < 2:
        return None
    shape = np.asarray(frames[0]).shape
    if len(shape) != 3 or shape[2] != 3 or any(np.asarray(f).shape != shape for f in frames):
        return None

    t = _merged(thresholds)
    object_colors = (observation.metadata or {}).get("object_colors") or {}
    if object_colors:
        return _evaluate_segmented(
            frames,
            {k: tuple(v) for k, v in object_colors.items()},
            action.object_id,
            t,
            expected_final,
            next_target_mask,
            next_object_id,
        )
    return _evaluate_unsegmented(
        frames, t, (observation.metadata or {}).get("target_region_px")
    )


# ---------------------------------------------------------------- visual output
def compose_side_by_side(
    panels: list[tuple[str, list[np.ndarray]]],
    *,
    expected: Optional[np.ndarray] = None,
    frame_indices=(0, -1),
    pad: int = 8,
) -> np.ndarray:
    """Labelled grid: one row per candidate, one column per `frame_indices` (+ expected).

    Size is deterministic: with `caption_h = 14`, cell = the largest panel image,
    `width  = pad + ncols * (cell_w + pad)` and
    `height = pad + nrows * (cell_h + caption_h + pad)`.
    Missing frames leave an empty cell. Returns (H, W, 3) uint8 RGB.
    """
    caption_h = 14
    font = ImageFont.load_default()
    ncols = len(frame_indices) + (1 if expected is not None else 0)
    nrows = max(1, len(panels))

    sizes = [np.asarray(f).shape[:2] for _, fs in panels for f in fs]
    if expected is not None:
        sizes.append(np.asarray(expected).shape[:2])
    cell_h = max((s[0] for s in sizes), default=1)
    cell_w = max((s[1] for s in sizes), default=1)

    canvas = Image.new(
        "RGB",
        (pad + ncols * (cell_w + pad), pad + nrows * (cell_h + caption_h + pad)),
        (40, 40, 44),
    )
    draw = ImageDraw.Draw(canvas)
    for r, (label, fs) in enumerate(panels):
        y = pad + r * (cell_h + caption_h + pad)
        cells: list[tuple[str, Optional[np.ndarray]]] = []
        for i in frame_indices:
            try:
                cells.append((f"frame {i}", fs[i]))
            except IndexError:
                cells.append((f"frame {i} (missing)", None))
        if expected is not None:
            cells.append(("expected", expected))
        for c, (caption, img) in enumerate(cells):
            x = pad + c * (cell_w + pad)
            text = f"{label} | {caption}" if c == 0 else caption
            draw.text((x, y + 2), text[:64], fill=(235, 235, 235), font=font)
            if img is not None:
                canvas.paste(Image.fromarray(np.asarray(img, dtype=np.uint8)), (x, y + caption_h))
    return np.asarray(canvas)


def save_png(array: np.ndarray, path) -> str:
    """Write an (H, W, 3) uint8 / (H, W) / bool array as a PNG. Returns the path."""
    a = np.asarray(array)
    if a.dtype == bool:
        a = a.astype(np.uint8) * 255
    Image.fromarray(a.astype(np.uint8)).save(str(path))
    return str(path)
