"""Provider-neutral `WorldModel` implementations (PAN.md SS10, SS11, SS12, SS25).

Three backends, all speaking only `pan.types` (never imported by anything
outside this file, per PAN.md SS11 -- the rest of the app must not know PAN's
SDK/HTTP shape):

    MockPanBackend     -- deterministic, offline, instant. Always available.
    RealPanBackend      -- the HTTP seam. The real HackCMU PAN route is not yet
                          known (PAN.md SS10 forbids inventing it); every
                          PAN-specific guess lives in the two functions marked
                          `# TODO(verify against docs/PAN_ACCESS.md)` below.
    CachingWorldModel   -- wraps any backend, persists completed rollouts to
                          disk so expensive/slow inference is never repeated
                          for the same (observation, action, history).

`get_world_model(...)` picks Real-if-available else Mock, optionally wrapped
in Caching.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import math
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

import numpy as np
import requests
from PIL import Image, ImageSequence

from pan.evaluate import DEFAULT_THRESHOLDS, segment_by_color
from pan.observation import project_points
from pan.types import Observation, PackingAction, SimulationRequest, SimulationResult, WorldModel

logger = logging.getLogger("pan")

RGB = tuple[int, int, int]

__all__ = [
    "MockPanBackend",
    "RealPanBackend",
    "CachingWorldModel",
    "get_world_model",
]


# =============================================================== mock backend

_DEFAULT_NUM_FRAMES = 8

# ---------------------------------------------------------------------------
# WHAT THE MOCK IS (and is not)
#
# It is a PICTURE of a plausible placement, drawn with numpy: the acted object's
# own pixels (shaded side faces included, because it segments with the same rule
# the evaluator uses) slide from where they are to where the RENDERER would draw
# the action's target pose. It models no physics at all -- no contact, no
# gravity, no collision response (PAN.md SS6: hard constraints come from
# `physics.validator`, never from here). It exists so the whole pipeline and the
# vision evaluator can be exercised offline, instantly and deterministically.
#
# Modes, reported as `metadata["mode"]`:
#   "placement" -- the acted object is segmentable in frame 0. Its whole blob
#                  eases (smoothstep) to the projected target pixel; vacated
#                  pixels become background; every other object is pixel-exact.
#   "arrival"   -- the acted object is off-screen or not segmentable (it is
#                  still on the table, outside the crop). A blob of its color
#                  fades in while sliding from the nearest image edge to the
#                  target. Beats pretending the whole scene wobbled, which the
#                  evaluator reads as "everything shifted".
#   "static"    -- the observation carries no color map for the acted object
#                  (e.g. a raw iPhone frame): nothing can be attributed to any
#                  object, so the mock holds the scene still (+ tiny noise)
#                  rather than inventing motion.
#
# Mock-only embellishment, driven by `options["physics_hints"]` (the physics
# WARNING types for the acted object, passed in by `pan/rollouts.py`): a hinted
# UNSTABLE_STACK / UNSTABLE_SUPPORT_CHAIN makes the blob slide on past the
# target and flatten in the last third of the rollout; SOFT_COMPRESSION squashes
# its height. This is a DRAWING of the warning the deterministic layer already
# produced -- it is not evidence of anything, and `metadata["hints_applied"]`
# says when it happened.
# ---------------------------------------------------------------------------

# Fixed, non-physical affine used only when the observation has NO viewpoint
# (an iPhone frame): it only has to be a *consistent* world (x, z) -> pixel map.
# Rendered observations use `pan.observation.project_points` instead -- the
# renderer's own pinhole math -- so the blob lands where the digital twin would
# have drawn the object.
# x in [-HALF, HALF] m -> column [0, W-1] (left/right); z in [-HALF, HALF] m
# -> row [H-1, 0] (near/-Z at the bottom of frame, far/+Z at the top).
_WORLD_HALF_EXTENT_M = 1.0
_NOISE_MAGNITUDE = 2  # +/- uint8 levels added to unprotected pixels per frame
_HINT_TOPPLE = ("UNSTABLE_STACK", "UNSTABLE_SUPPORT_CHAIN")
_HINT_SQUASH = "SOFT_COMPRESSION"
_TOPPLE_FLATTEN = 2.2  # the toppled box's short side is squashed by this
_SQUASH_FRACTION = 0.15
_ARRIVAL_BLOB_FRAC = 0.12  # last-resort arrival blob size, as a fraction of the frame
_ARRIVAL_ALPHA0 = 0.25  # opacity of the arriving blob in the first moved frame
_FLAT_BG_TOL = 8  # per-channel distance from the modal color that still counts as flat background


def _world_to_pixel(x: float, z: float, width: int, height: int) -> tuple[int, int]:
    half = _WORLD_HALF_EXTENT_M
    u = (x + half) / (2 * half)
    v = (half - z) / (2 * half)
    col = int(round(min(1.0, max(0.0, u)) * (width - 1)))
    row = int(round(min(1.0, max(0.0, v)) * (height - 1)))
    return col, row


def _content_seed(frame0: np.ndarray, action_text: str, options: dict) -> int:
    """Deterministic seed from content only -- excludes request_id on purpose
    (PAN.md/spec: identical inputs must give byte-identical frames across
    separate requests). `options["seed"]` overrides when the caller wants
    explicit control."""
    if "seed" in options:
        return int(options["seed"])
    h = hashlib.sha256()
    h.update(np.ascontiguousarray(frame0).tobytes())
    h.update(action_text.encode("utf-8"))
    h.update(json.dumps(options, sort_keys=True, default=str).encode("utf-8"))
    return int(h.hexdigest()[:16], 16)


def _background_color(frame: np.ndarray) -> RGB:
    """Modal color of the frame (pack RGB into one uint32 so this is a 1-D
    unique instead of a lexsort over H*W rows -- ~10x cheaper at 512^2)."""
    p = frame.astype(np.uint32)
    packed = (p[..., 0] << 16) | (p[..., 1] << 8) | p[..., 2]
    values, counts = np.unique(packed.ravel(), return_counts=True)
    v = int(values[int(np.argmax(counts))])
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


def _add_noise(frame: np.ndarray, rng: np.random.Generator, protect: Optional[np.ndarray] = None) -> np.ndarray:
    """Tiny sensor-ish noise on the BACKGROUND only: every `protect`ed pixel --
    in practice everything that is not flat background, objects and their edges
    included -- is left byte-exact, so the evaluator never reads noise as an
    object having moved or changed shape. The rng is drawn identically either
    way, keeping the frame sequence deterministic regardless of the mask.

    One luminance plane per frame rather than three independent channels: a third
    of the random draws (this is the mock's hottest loop) and closer to what
    sensor noise looks like anyway."""
    noise = rng.integers(-_NOISE_MAGNITUDE, _NOISE_MAGNITUDE + 1, size=frame.shape[:2], dtype=np.int16)
    if protect is not None:
        noise[protect] = 0  # zero the noise, not the result: one (H, W) pass instead of three
    out = frame.astype(np.int16)
    out += noise[..., None]
    np.clip(out, 0, 255, out=out)
    return out.astype(np.uint8)


def _local_background(frame: np.ndarray, mask: np.ndarray, pad: int = 6) -> RGB:
    """Modal color of the strip just BELOW the object -- whatever it was standing
    on (the table, the case floor). Filling the pixels it vacates with the
    frame's GLOBAL modal color instead leaves a bright hole in the table."""
    ys, xs = np.nonzero(mask)
    h = frame.shape[0]
    r0 = min(h, int(ys.max()) + 1)
    strip = frame[r0 : min(h, r0 + pad), int(xs.min()) : int(xs.max()) + 1]
    return _background_color(strip) if strip.size else _background_color(frame)


def _flat_background(frame: np.ndarray, background: RGB) -> np.ndarray:
    """Pixels close enough to the modal color to be plain background/table.

    This -- not "everything that is not an object" -- is where the mock's noise
    is allowed to land. An edge pixel sitting just OUTSIDE `color_tol` of some
    palette color would otherwise get jittered INSIDE it, and a single such
    pixel is enough to blow up the bounding box `pan.evaluate` measures.
    """
    d = np.abs(frame.astype(np.int16) - np.asarray(background, dtype=np.int16)).max(axis=-1)
    return d <= _FLAT_BG_TOL


def _smoothstep(t: float) -> float:
    """Eased 0->1 so the motion reads as a placement (slow start, slow stop)."""
    return t * t * (3.0 - 2.0 * t)


def _late_ramp(i: int, num_frames: int) -> float:
    """0 until the last third of the rollout, then 0 -> 1 across it."""
    start = num_frames - max(1, num_frames // 3)
    if num_frames <= 1 or i < start:
        return 0.0
    return (i - start + 1) / float(num_frames - start)


def _slide_offset(seed: int, width: int, height: int) -> tuple[float, float]:
    """Extra post-arrival slide for a hinted topple: 10-15% of the image diagonal
    along one of four diagonal directions, both picked from the content seed (so
    it is deterministic, and different actions do not all slide the same way)."""
    frac = 0.10 + 0.05 * (((seed >> 8) % 101) / 100.0)
    step = frac * math.hypot(width, height) / math.sqrt(2.0)
    return (step if seed & 1 else -step), (step if seed & 2 else -step)


def _toppled_size(bw: int, bh: int) -> tuple[int, int]:
    """The blob's bbox laid flat: its long side becomes the width, its short side
    is squashed by 2.2. Literally inverting w/h would be a no-op for a near-square
    blob, and widening it past its own long side looks absurd on screen; this
    only ever shrinks the silhouette, and the resulting aspect change is >= 1.2
    for ANY starting aspect, so `pan.evaluate`'s test fires either way."""
    long_side, short_side = max(bw, bh), min(bw, bh)
    return long_side, max(2, int(round(short_side / _TOPPLE_FLATTEN)))


def _clamped_center(cu: float, cv: float, shape: tuple[int, int], width: int, height: int) -> tuple[float, float]:
    """Keep a blob of `shape`=(w, h) fully in frame. A mock that slides its own
    subject out of view teaches the evaluator nothing, and the hinted slide is
    deliberately large."""
    bw, bh = shape
    cu = min(max(cu, bw / 2.0), width - bw / 2.0) if bw < width else width / 2.0
    cv = min(max(cv, bh / 2.0), height - bh / 2.0) if bh < height else height / 2.0
    return cu, cv


def _paint_box(
    frame: np.ndarray, center: tuple[float, float], size: tuple[int, int], color: RGB, alpha: float = 1.0
) -> np.ndarray:
    """Fill an axis-aligned `size`=(w, h) box centered on `center` (clipped to the
    frame) with `color` at `alpha` opacity. Returns the painted mask."""
    h, w = frame.shape[:2]
    bw, bh = size
    c0, r0 = int(round(center[0] - bw / 2.0)), int(round(center[1] - bh / 2.0))
    r_lo, r_hi = max(0, r0), min(h, r0 + bh)
    c_lo, c_hi = max(0, c0), min(w, c0 + bw)
    mask = np.zeros((h, w), dtype=bool)
    if r_hi <= r_lo or c_hi <= c_lo:
        return mask
    mask[r_lo:r_hi, c_lo:c_hi] = True
    if alpha >= 1.0:
        frame[r_lo:r_hi, c_lo:c_hi] = color
    else:
        under = frame[r_lo:r_hi, c_lo:c_hi].astype(np.float32)
        blend = under * (1.0 - alpha) + np.asarray(color, dtype=np.float32) * alpha
        frame[r_lo:r_hi, c_lo:c_hi] = np.round(blend).astype(np.uint8)
    return mask


def _target_pixel(obs: Observation, action: PackingAction, width: int, height: int) -> tuple[float, float]:
    """Pixel the acted object should end up at. Rendered observations carry the
    renderer's own `Viewpoint`, so project the target pose with the renderer's
    pinhole math (`pan.observation.project_points`) and the blob lands exactly
    where the digital twin would have drawn it. Otherwise fall back to the
    fixed non-physical affine."""
    viewpoint = (obs.metadata or {}).get("viewpoint")
    if viewpoint:
        u, v = project_points([action.target_position], viewpoint)[0]
        return float(u), float(v)
    col, row = _world_to_pixel(action.target_position[0], action.target_position[2], width, height)
    return float(col), float(row)


def _arrival_blob_size(
    obs: Observation, options: dict, masks: dict[str, np.ndarray], acted: str, width: int, height: int
) -> tuple[tuple[int, int], str]:
    """Pixel (w, h) for a synthesized arrival blob, best source first: an explicit
    `options["acted_bbox_px"]`, the renderer's projected bbox for the object, the
    median of the other objects' bboxes/blobs, else a fraction of the frame."""
    box = options.get("acted_bbox_px")
    if box:
        x0, y0, x1, y1 = (float(v) for v in box)
        return (max(2, int(round(x1 - x0))), max(2, int(round(y1 - y0)))), "options.acted_bbox_px"

    boxes = (obs.metadata or {}).get("object_bboxes") or {}
    if acted in boxes:
        x0, y0, x1, y1 = boxes[acted]
        return (max(2, int(round(x1 - x0))), max(2, int(round(y1 - y0)))), "metadata.object_bboxes"

    sizes = [(x1 - x0, y1 - y0) for oid, (x0, y0, x1, y1) in boxes.items() if oid != acted]
    source = "median_other_object_bboxes"
    if not sizes:
        source = "median_other_segmented_blobs"
        for oid, mask in masks.items():
            if oid == acted or not mask.any():
                continue
            ys, xs = np.nonzero(mask)
            sizes.append((int(xs.max() - xs.min()) + 1, int(ys.max() - ys.min()) + 1))
    if sizes:
        return (
            max(2, int(np.median([s[0] for s in sizes]))),
            max(2, int(np.median([s[1] for s in sizes]))),
        ), source
    return (max(2, int(width * _ARRIVAL_BLOB_FRAC)), max(2, int(height * _ARRIVAL_BLOB_FRAC))), "frame_fraction"


def _arrival_start(obs: Observation, acted: str, target: tuple[float, float], width: int, height: int) -> tuple[float, float]:
    """Where an arriving blob slides in FROM: the acted object's own projected
    bbox center clamped into the frame -- for an off-screen object that is
    exactly the nearest image edge -- else the nearest edge point to `target`."""
    box = ((obs.metadata or {}).get("object_bboxes") or {}).get(acted)
    if box:
        return (
            float(np.clip((box[0] + box[2]) / 2.0, 0.0, width - 1.0)),
            float(np.clip((box[1] + box[3]) / 2.0, 0.0, height - 1.0)),
        )
    u, v = target
    edges = [(u, (0.0, v)), (width - 1.0 - u, (width - 1.0, v)), (v, (u, 0.0)), (height - 1.0 - v, (u, height - 1.0))]
    return min(edges, key=lambda e: e[0])[1]


class MockPanBackend:
    """Offline stand-in for PAN. Deterministic given (frame0, action.text,
    options): same inputs -> byte-identical frames, always, across processes.

    MOCK, not a simulator: it draws a plausible-looking placement and claims
    nothing about physics. See the module comment above for the three modes and
    the physics-hint embellishment."""

    name = "mock"
    supports_continuation = True

    def available(self) -> bool:
        return True

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        start = time.perf_counter()
        obs = request.observation
        action = request.action
        options = dict(request.options or {})
        num_frames = max(1, int(options.get("num_frames", _DEFAULT_NUM_FRAMES)))

        if request.history:
            base = request.history[-1].final_frame
            frame0 = base if base is not None else obs.image
        else:
            frame0 = obs.image
        frame0 = np.asarray(frame0, dtype=np.uint8)

        seed = _content_seed(frame0, action.text, options)
        rng = np.random.default_rng(seed)

        frames, extra = self._rollout(frame0, obs, action, options, num_frames, rng, seed)

        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "backend=mock request_id=%s status=complete mode=%s latency_ms=%.2f",
            request.request_id, extra.get("mode"), latency_ms,
        )
        return SimulationResult(
            request_id=request.request_id,
            status="complete",
            backend="mock",
            frames=frames,
            latency_ms=latency_ms,
            metadata={"num_frames": num_frames, "acted_object": action.object_id, **extra},
        )

    def _rollout(
        self,
        frame0: np.ndarray,
        obs: Observation,
        action: PackingAction,
        options: dict,
        num_frames: int,
        rng: np.random.Generator,
        seed: int,
    ) -> tuple[list[np.ndarray], dict[str, Any]]:
        """Pick a mode and synthesize its frames. See the module comment above
        for what each mode means. Segmentation happens ONCE here, with the
        evaluator's own rule, so shaded faces travel with the object and the two
        halves of the pipeline can never disagree about which pixels it owns."""
        h, w = frame0.shape[:2]
        colors: dict[str, RGB] = {
            oid: tuple(int(v) for v in c)
            for oid, c in ((obs.metadata or {}).get("object_colors") or {}).items()
        }
        acted = action.object_id
        if acted not in colors:
            return self._rollout_static(frame0, num_frames, rng), {
                "mode": "static",
                "hints_applied": [],
                "note": (
                    "no object_colors entry for the acted object, so no pixel can be attributed "
                    "to it: the mock holds the scene still rather than faking motion."
                ),
            }

        hints = [str(x) for x in (options.get("physics_hints") or [])]
        topple = any(hint in _HINT_TOPPLE for hint in hints)
        squash = _HINT_SQUASH in hints
        applied = [hint for hint in hints if hint in _HINT_TOPPLE or hint == _HINT_SQUASH]

        # ponytail: this ONE shared call dominates the mock's cost (~112 ms of a
        # ~170 ms 8-frame 512^2 rollout; ~79 of 87 ms at the 384^2 fixture size).
        # It is deliberately the evaluator's own function and is called once, not
        # per frame. If the budget ever needs to be under 150 ms at 512^2, make
        # `pan.evaluate.segment_by_color` cheaper (reject background pixels before
        # the 7-way distance stack) -- do NOT fork the rule in here.
        masks = segment_by_color(frame0, colors, DEFAULT_THRESHOLDS["color_tol"])
        acted_mask = masks[acted]
        background = _background_color(frame0)
        # Noise may only touch flat background: that protects every object's
        # pixels (they are nowhere near the modal color) AND every edge pixel
        # around them, which is the part that actually matters -- see
        # `_flat_background`.
        protect = ~_flat_background(frame0, background)

        target = _target_pixel(obs, action, w, h)
        slide = _slide_offset(seed, w, h) if topple else (0.0, 0.0)
        meta: dict[str, Any] = {
            "target_px": [round(target[0], 2), round(target[1], 2)],
            "hints_applied": applied,
        }

        framed = ((obs.metadata or {}).get("object_visible") or {}).get(acted, True)
        if framed and int(acted_mask.sum()) >= DEFAULT_THRESHOLDS["min_area"]:
            meta["mode"] = "placement"
            frames = self._rollout_placement(
                frame0, acted_mask, protect, colors[acted], target,
                num_frames, rng, slide, topple, squash,
            )
        else:
            size, source = _arrival_blob_size(obs, options, masks, acted, w, h)
            meta.update(mode="arrival", arrival_size_px=list(size), arrival_size_source=source)
            frames = self._rollout_arrival(
                frame0, protect, colors[acted], size,
                _arrival_start(obs, acted, target, w, h), target, num_frames, rng, slide, topple, squash,
            )
        return frames, meta

    @staticmethod
    def _rollout_placement(
        frame0: np.ndarray,
        acted_mask: np.ndarray,
        protect: np.ndarray,
        base_color: RGB,
        target: tuple[float, float],
        num_frames: int,
        rng: np.random.Generator,
        slide: tuple[float, float],
        topple: bool,
        squash: bool,
    ) -> list[np.ndarray]:
        """Translate the acted object's WHOLE blob (original pixel values, so its
        shaded faces come along) from its frame-0 centroid to `target`."""
        h, w = frame0.shape[:2]
        rows, cols = np.nonzero(acted_mask)
        pixels = frame0[rows, cols]
        cu, cv = float(cols.mean()), float(rows.mean())
        box = (int(cols.max() - cols.min()) + 1, int(rows.max() - rows.min()) + 1)
        bottom = int(rows.max())

        plate = frame0.copy()
        plate[rows, cols] = _local_background(frame0, acted_mask)  # what it was lying on
        topple_from = num_frames - max(1, num_frames // 3)

        frames = [frame0]
        for i in range(1, num_frames):
            t = _smoothstep(i / (num_frames - 1)) if num_frames > 1 else 1.0
            ramp = _late_ramp(i, num_frames)
            toppling = topple and i >= topple_from
            shape = _toppled_size(*box) if toppling else box
            ccol, crow = _clamped_center(
                cu + (target[0] - cu) * t + slide[0] * ramp,
                cv + (target[1] - cv) * t + slide[1] * ramp,
                shape, w, h,
            )

            frame = plate.copy()
            if toppling:
                painted = _paint_box(frame, (ccol, crow), shape, base_color)
            else:
                new_rows = rows + int(round(crow - cv))
                new_cols = cols + int(round(ccol - cu))
                if squash:
                    floor = bottom + int(round(crow - cv))
                    new_rows = np.round(floor - (floor - new_rows) * (1.0 - _SQUASH_FRACTION * t)).astype(int)
                keep = (new_rows >= 0) & (new_rows < h) & (new_cols >= 0) & (new_cols < w)
                new_rows, new_cols = new_rows[keep], new_cols[keep]
                frame[new_rows, new_cols] = pixels[keep]
                painted = np.zeros((h, w), dtype=bool)
                painted[new_rows, new_cols] = True
            # The blob is drawn IN FRONT of the other objects, matching the
            # renderer's painter's algorithm for an object placed on top of the
            # pile: measured against a ground-truth render of the final pose, the
            # acted object keeps its silhouette (area within ~1%) and a partly
            # covered neighbour's centroid moves about as far as it really does
            # (8.8 px vs 9.9 px for laptop-under-shoe). Drawing it behind instead
            # cost the acted object ~30% of its pixels, which the evaluator then
            # (correctly, but misleadingly) read as it being occluded.
            frames.append(_add_noise(frame, rng, protect | painted))
        return frames

    @staticmethod
    def _rollout_arrival(
        frame0: np.ndarray,
        protect: np.ndarray,
        base_color: RGB,
        size: tuple[int, int],
        start: tuple[float, float],
        target: tuple[float, float],
        num_frames: int,
        rng: np.random.Generator,
        slide: tuple[float, float],
        topple: bool,
        squash: bool,
    ) -> list[np.ndarray]:
        """The acted object is not in frame 0 (it is still on the table, outside
        the crop), so synthesize its ARRIVAL: a blob of its color fading in while
        it slides from `start` (the nearest image edge) to `target`."""
        bw, bh = size
        topple_from = num_frames - max(1, num_frames // 3)
        frames = [frame0]
        for i in range(1, num_frames):
            t = _smoothstep(i / (num_frames - 1)) if num_frames > 1 else 1.0
            ramp = _late_ramp(i, num_frames)
            if topple and i >= topple_from:
                shape = _toppled_size(bw, bh)
            else:
                shape = (bw, max(2, int(round(bh * (1.0 - _SQUASH_FRACTION * t))))) if squash else (bw, bh)
            center = _clamped_center(
                start[0] + (target[0] - start[0]) * t + slide[0] * ramp,
                start[1] + (target[1] - start[1]) * t + slide[1] * ramp,
                shape, frame0.shape[1], frame0.shape[0],
            )

            frame = frame0.copy()
            alpha = _ARRIVAL_ALPHA0 + (1.0 - _ARRIVAL_ALPHA0) * t  # exactly 1.0 on the last frame
            painted = _paint_box(frame, center, shape, base_color, alpha)
            frames.append(_add_noise(frame, rng, protect | painted))
        return frames

    @staticmethod
    def _rollout_static(frame0: np.ndarray, num_frames: int, rng: np.random.Generator) -> list[np.ndarray]:
        """Nothing attributable to move: hold the scene, breathe tiny noise over
        it. (Replaces the old whole-image "wobble", which the evaluator correctly
        read as every object shifting -> medium risk for every candidate.)"""
        return [frame0] + [_add_noise(frame0, rng) for _ in range(num_frames - 1)]

    def persist(self, result: SimulationResult, out_dir: str | os.PathLike) -> SimulationResult:
        """Write frame_00.png .. frame_NN.png + rollout.gif into `out_dir`.
        Returns a copy of `result` with video_path/final_frame_path filled."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if not result.frames:
            return result

        frame_paths = []
        for i, frame in enumerate(result.frames):
            p = out_dir / f"frame_{i:02d}.png"
            Image.fromarray(np.asarray(frame, dtype=np.uint8)).save(p)
            frame_paths.append(str(p))

        gif_path = out_dir / "rollout.gif"
        images = [Image.fromarray(np.asarray(f, dtype=np.uint8)) for f in result.frames]
        images[0].save(gif_path, save_all=True, append_images=images[1:], duration=150, loop=0)

        return replace(result, video_path=str(gif_path), final_frame_path=frame_paths[-1])


# =============================================================== real backend

_RETRYABLE_CONNECTION_ERRORS = (requests.exceptions.ConnectionError, requests.exceptions.Timeout)


def _backoff_seconds(attempt: int) -> float:
    return min(0.05 * (2 ** (attempt - 1)), 1.0)


def _build_payload(request: SimulationRequest) -> dict:
    """Pack a SimulationRequest into the JSON body sent to PAN.

    # TODO(verify against docs/PAN_ACCESS.md): the real HackCMU PAN request
    shape is not yet known (PAN.md SS10 -- do not invent an endpoint or
    payload contract). This currently assumes: current frame as base64 PNG,
    the grounded action as plain text, and free-form `options` passed through
    verbatim. Adjust field names/structure once the real docs/starter code
    are found; nothing else in this module should need to change.
    """
    buf = io.BytesIO()
    Image.fromarray(np.asarray(request.observation.image, dtype=np.uint8)).save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    history_b64 = []
    for prior in request.history:
        frame = prior.final_frame
        if frame is not None:
            hbuf = io.BytesIO()
            Image.fromarray(np.asarray(frame, dtype=np.uint8)).save(hbuf, format="PNG")
            history_b64.append(base64.b64encode(hbuf.getvalue()).decode("ascii"))

    return {
        "image_png_base64": image_b64,
        "action_text": request.action.text,
        "history_frames_png_base64": history_b64,
        "options": dict(request.options),
    }


def _decode_png_b64(b64: str) -> np.ndarray:
    raw = base64.b64decode(b64)
    return np.array(Image.open(io.BytesIO(raw)).convert("RGB"), dtype=np.uint8)


def _decode_animated_bytes(data: bytes) -> list[np.ndarray]:
    im = Image.open(io.BytesIO(data))
    return [np.array(frame.convert("RGB"), dtype=np.uint8) for frame in ImageSequence.Iterator(im)]


def _parse_response(payload) -> list[np.ndarray]:
    """Normalize a PAN response body into a list of (H, W, 3) uint8 frames.

    # TODO(verify against docs/PAN_ACCESS.md): the real response shape is not
    yet known. This currently accepts either `{"frames": [base64 PNG, ...]}`
    or `{"video_url": "...gif"}` (downloaded and decoded with Pillow). An
    `.mp4` video_url raises -- no cv2/imageio available in this environment,
    so MP4 decoding is not supported; callers map that to status="failed".
    """
    if isinstance(payload, bytes):
        return _decode_animated_bytes(payload)

    if isinstance(payload, dict):
        if "frames" in payload:
            return [_decode_png_b64(b) for b in payload["frames"]]
        if "video_url" in payload:
            url = payload["video_url"]
            if url.lower().endswith(".mp4"):
                raise RuntimeError("mp4 decoding not available")
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
            return _decode_animated_bytes(resp.content)

    raise ValueError("unrecognized PAN response shape")


class RealPanBackend:
    """HTTP seam for the real PAN service.

    Configured from environment variables. This is OUR convention for the
    HackCMU integration, not something IFM documents:

        PAN_API_KEY       -- secret, sent as `Authorization: Bearer <key>`
        PAN_BASE_URL       -- e.g. "https://pan.example.com"
        PAN_MODEL          -- optional model/version identifier
        PAN_TIMEOUT_S      -- per-request timeout, default 60
        PAN_ENDPOINT_PATH  -- e.g. "/v1/simulate"; its absence means nobody
                              has confirmed the real route yet, so `available()`
                              stays False and `simulate` refuses to guess a URL.

    Env vars are read once, at construction time.
    """

    name = "pan"
    supports_continuation = False  # unverified; flip once continuation is confirmed against real docs

    _MAX_RETRIES = 2  # up to 2 retries (3 attempts total) on 429/5xx/connection errors

    def __init__(self) -> None:
        self.api_key = os.environ.get("PAN_API_KEY")
        self.base_url = os.environ.get("PAN_BASE_URL")
        self.model = os.environ.get("PAN_MODEL")
        self.endpoint_path = os.environ.get("PAN_ENDPOINT_PATH")
        self.timeout_s = float(os.environ.get("PAN_TIMEOUT_S", "60"))
        self.session = requests.Session()

    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.endpoint_path)

    def _unavailable_reason(self) -> str:
        missing = [
            name
            for name, val in (
                ("PAN_API_KEY", self.api_key),
                ("PAN_BASE_URL", self.base_url),
                ("PAN_ENDPOINT_PATH", self.endpoint_path),
            )
            if not val
        ]
        return "PAN backend unavailable: unset " + ", ".join(missing)

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        start = time.perf_counter()

        if not self.available():
            latency_ms = (time.perf_counter() - start) * 1000.0
            logger.info(
                "backend=pan request_id=%s status=unavailable latency_ms=%.2f",
                request.request_id, latency_ms,
            )
            return SimulationResult(
                request_id=request.request_id,
                status="unavailable",
                backend="pan",
                error=self._unavailable_reason(),
                latency_ms=latency_ms,
            )

        url = f"{self.base_url.rstrip('/')}{self.endpoint_path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-Request-Id": request.request_id,
            "Content-Type": "application/json",
        }
        payload = _build_payload(request)
        if self.model:
            payload["model"] = self.model

        attempts = 0
        last_error = "unknown error"
        max_attempts = 1 + self._MAX_RETRIES
        while attempts < max_attempts:
            attempts += 1
            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=self.timeout_s)
            except _RETRYABLE_CONNECTION_ERRORS as exc:
                last_error = f"connection error ({type(exc).__name__})"
                if attempts < max_attempts:
                    time.sleep(_backoff_seconds(attempts))
                    continue
                return self._fail(request, start, last_error, attempts)

            if resp.status_code == 200:
                try:
                    try:
                        body = resp.json()
                    except ValueError:
                        body = resp.content
                    frames = _parse_response(body)
                except Exception as exc:
                    error = "mp4 decoding not available" if "mp4" in str(exc).lower() else \
                        f"failed to parse PAN response ({type(exc).__name__})"
                    return self._fail(request, start, error, attempts)
                latency_ms = (time.perf_counter() - start) * 1000.0
                logger.info(
                    "backend=pan request_id=%s status=complete latency_ms=%.2f attempts=%d",
                    request.request_id, latency_ms, attempts,
                )
                return SimulationResult(
                    request_id=request.request_id,
                    status="complete",
                    backend="pan",
                    frames=frames,
                    latency_ms=latency_ms,
                    metadata={"attempts": attempts},
                )

            retryable = resp.status_code == 429 or resp.status_code >= 500
            # Never include the response body/headers/URL query string -- they
            # can echo back tokens (PAN.md SS25). Status code only.
            last_error = f"pan backend returned HTTP {resp.status_code}"
            if retryable and attempts < max_attempts:
                time.sleep(_backoff_seconds(attempts))
                continue
            return self._fail(request, start, last_error, attempts)

        return self._fail(request, start, last_error, attempts)  # pragma: no cover - unreachable

    def _fail(self, request: SimulationRequest, start: float, error: str, attempts: int) -> SimulationResult:
        latency_ms = (time.perf_counter() - start) * 1000.0
        logger.info(
            "backend=pan request_id=%s status=failed latency_ms=%.2f attempts=%d",
            request.request_id, latency_ms, attempts,
        )
        return SimulationResult(
            request_id=request.request_id,
            status="failed",
            backend="pan",
            error=error,
            latency_ms=latency_ms,
            metadata={"attempts": attempts},
        )


# ============================================================ caching wrapper

class CachingWorldModel:
    """Wraps any WorldModel with an on-disk cache of completed rollouts.

    Key = sha256(observation PNG bytes, action.text, action.object_id,
    target position + rotation each rounded to 1e-4, sorted options, and the
    request_ids of `history`, in order). Failures/unavailable results are
    never cached (PAN.md SS12: don't rerun expensive inference, but also
    don't cache a state that should be retried).
    """

    def __init__(self, inner: WorldModel, cache_dir: str | os.PathLike = ".pan_cache") -> None:
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.name = f"cache({inner.name})"
        self.supports_continuation = inner.supports_continuation

    def available(self) -> bool:
        return self.inner.available()

    def simulate(self, request: SimulationRequest) -> SimulationResult:
        key = self._cache_key(request)
        cached = self._load(key, request.request_id)
        if cached is not None:
            logger.info("backend=%s request_id=%s status=complete cache_hit=true", self.inner.name, request.request_id)
            return cached

        result = self.inner.simulate(request)
        if result.status == "complete":
            self._save(key, result)
        return result

    def _cache_key(self, request: SimulationRequest) -> str:
        h = hashlib.sha256()
        buf = io.BytesIO()
        Image.fromarray(np.asarray(request.observation.image, dtype=np.uint8)).save(buf, format="PNG")
        h.update(buf.getvalue())
        action = request.action
        h.update(action.text.encode("utf-8"))
        h.update(action.object_id.encode("utf-8"))
        pose = {
            "position": tuple(round(float(v), 4) for v in action.target_position),
            "rotation": tuple(round(float(v), 4) for v in action.target_rotation),
        }
        h.update(json.dumps(pose, sort_keys=True).encode("utf-8"))
        h.update(json.dumps(dict(request.options), sort_keys=True, default=str).encode("utf-8"))
        h.update(",".join(r.request_id for r in request.history).encode("utf-8"))
        return h.hexdigest()

    def _load(self, key: str, request_id: str) -> Optional[SimulationResult]:
        d = self.cache_dir / key
        meta_path = d / "meta.json"
        if not meta_path.exists():
            return None
        start = time.perf_counter()
        meta = json.loads(meta_path.read_text())
        frame_paths = sorted(d.glob("frame_*.png"))
        frames = [np.array(Image.open(p).convert("RGB"), dtype=np.uint8) for p in frame_paths]
        latency_ms = (time.perf_counter() - start) * 1000.0
        return SimulationResult(
            request_id=request_id,
            status="complete",
            backend=meta.get("backend", self.inner.name),
            frames=frames,
            video_path=meta.get("video_path"),
            final_frame_path=meta.get("final_frame_path"),
            latency_ms=latency_ms,
            cache_hit=True,
            metadata=meta.get("metadata", {}),
        )

    def _save(self, key: str, result: SimulationResult) -> None:
        d = self.cache_dir / key
        d.mkdir(parents=True, exist_ok=True)
        for i, frame in enumerate(result.frames):
            Image.fromarray(np.asarray(frame, dtype=np.uint8)).save(d / f"frame_{i:02d}.png")
        meta = {
            "backend": result.backend,
            "video_path": result.video_path,
            "final_frame_path": result.final_frame_path,
            "metadata": result.metadata,
        }
        (d / "meta.json").write_text(json.dumps(meta))


# =========================================================================== factory

def get_world_model(prefer: str = "auto", cache_dir: Optional[str | os.PathLike] = None) -> WorldModel:
    """Pick a backend: "mock" | "pan" | "auto" (Real if available() else Mock).
    Wrapped in CachingWorldModel iff `cache_dir` is given."""
    if prefer == "mock":
        model: WorldModel = MockPanBackend()
    elif prefer == "pan":
        model = RealPanBackend()
    elif prefer == "auto":
        real = RealPanBackend()
        model = real if real.available() else MockPanBackend()
    else:
        raise ValueError(f"unknown prefer={prefer!r}, expected 'auto', 'mock', or 'pan'")

    if cache_dir is not None:
        model = CachingWorldModel(model, cache_dir)
    return model
