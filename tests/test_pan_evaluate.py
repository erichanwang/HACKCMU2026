"""Tests for `pan.evaluate`: synthetic Pillow frames -> RiskSignals.

Frames are drawn here (grey background, flat colored rectangles, optional face
shading) so the evaluator is tested without importing pan.observation /
pan.world_model, which other agents are writing concurrently.
"""
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from pan.evaluate import (
    DEFAULT_THRESHOLDS,
    compose_side_by_side,
    evaluate_rollout,
    sample_frames,
    save_png,
    segment_by_color,
)
from pan.types import Observation, PackingAction, SimulationResult

SIZE = (128, 128)
BG = (128, 128, 130)
RED = (200, 40, 40)
BLUE = (40, 60, 200)
GREEN = (40, 180, 60)
COLORS = {"red": RED, "blue": BLUE, "green": GREEN}


def draw(rects, size=SIZE, bg=BG):
    """rects: [((x, y, w, h), color, shade_right_half?)] -> (H, W, 3) uint8."""
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    for rect in rects:
        (x, y, w, h), color = rect[0], rect[1]
        shade = rect[2] if len(rect) > 2 else False
        d.rectangle([x, y, x + w - 1, y + h - 1], fill=color)
        if shade:  # "face shading": right half of the object at 80% brightness
            dark = tuple(int(c * 0.8) for c in color)
            d.rectangle([x + w // 2, y, x + w - 1, y + h - 1], fill=dark)
    return np.asarray(img)


def obs(colors=COLORS, **metadata):
    md = dict(metadata)
    if colors:
        md["object_colors"] = dict(colors)
    return Observation(
        image=draw([]),
        scene_id="scene-1",
        source="rendered" if colors else "ios_rgb",
        object_ids=sorted(colors or []),
        metadata=md,
    )


def sim(frames, status="complete"):
    return SimulationResult(request_id="req", status=status, backend="mock", frames=list(frames))


def act(object_id="red"):
    return PackingAction(object_id=object_id, target_position=(0.0, 0.0, 0.0))


def translating_red(n=6, step=6, blue_step=0):
    """Red 20x20 slides right `step` px/frame; blue 20x20 drifts `blue_step` px/frame."""
    return [
        draw([((10 + step * i, 10, 20, 20), RED), ((80 + blue_step * i, 80, 20, 20), BLUE)])
        for i in range(n)
    ]


class TestSegmentation(unittest.TestCase):
    def test_segment_assigns_objects_and_background(self):
        masks = segment_by_color(draw([((10, 10, 20, 20), RED)]), COLORS)
        self.assertEqual(sorted(masks), ["blue", "green", "red"])
        self.assertEqual(int(masks["red"].sum()), 400)
        self.assertEqual(int(masks["blue"].sum()), 0)
        # background grey is farther than tol from every palette color
        self.assertEqual(int(sum(m.sum() for m in masks.values())), 400)

    def test_shading_robustness(self):
        """(9) Faces darkened x0.8 still segment as the same object."""
        frame = draw([((10, 10, 20, 20), RED, True)])
        masks = segment_by_color(frame, COLORS, DEFAULT_THRESHOLDS["color_tol"])
        self.assertEqual(int(masks["red"].sum()), 400)  # both the lit and the shaded half
        dark_px = frame[15, 25]
        self.assertTrue((dark_px != np.array(RED)).any())  # it really was shaded

    def test_teal_vs_shaded_blue_documented_pair(self):
        """(12) FIXES.md #3 / pan/observation.py's documented collision:
        toiletry_bag's teal and headphones_case's side-shaded (0.82x) blue are
        <60 raw-RGB units apart, yet must still segment as two objects."""
        TEAL = (0, 128, 128)
        BLUE = (0, 130, 200)
        SHADED_BLUE = tuple(round(c * 0.82) for c in BLUE)
        # guard the premise: shaded blue really is closer to teal than to its
        # own true color under plain RGB distance -- that's the bug.
        self.assertLess(sum((a - b) ** 2 for a, b in zip(TEAL, SHADED_BLUE)) ** 0.5, 60.0)
        frame = draw([((10, 10, 20, 20), TEAL), ((60, 60, 20, 20), SHADED_BLUE)])
        masks = segment_by_color(frame, {"teal_obj": TEAL, "blue_obj": BLUE})
        self.assertEqual(int(masks["teal_obj"].sum()), 400)
        self.assertEqual(int(masks["blue_obj"].sum()), 400)

    def test_sample_frames(self):
        frames = translating_red(6)
        s = sample_frames(frames, k=5)
        self.assertEqual(len(s), 5)
        self.assertTrue(np.array_equal(s[0], frames[0]))
        self.assertTrue(np.array_equal(s[-1], frames[-1]))
        self.assertEqual(len(sample_frames(frames[:3], k=5)), 3)


class TestSegmentedRegime(unittest.TestCase):
    def test_acted_object_moves_alone(self):
        """(1) Red (acted) translates, blue stays -> no visible shift."""
        r = evaluate_rollout(sim(translating_red(6, 6)), obs(), act("red"))
        self.assertFalse(r.visible_shift)
        self.assertFalse(r.possible_topple)
        objects = r.evidence["objects"]
        self.assertAlmostEqual(objects["red"]["displacement_px"], 30.0, places=3)
        self.assertAlmostEqual(objects["blue"]["displacement_px"], 0.0, places=3)
        self.assertAlmostEqual(objects["blue"]["displacement_frac_diag"], 0.0, places=5)
        self.assertEqual(r.evidence["shifted_objects"], [])
        self.assertEqual(r.occlusion_risk, 0.0)  # area proxy: red never shrinks
        self.assertAlmostEqual(r.confidence, 0.8, places=5)

    def test_non_acted_drift_is_visible_shift(self):
        """(2) Blue also drifts 10 px -> visible_shift True."""
        r = evaluate_rollout(sim(translating_red(6, 6, blue_step=2)), obs(), act("red"))
        self.assertTrue(r.visible_shift)
        self.assertEqual(r.evidence["shifted_objects"], ["blue"])
        self.assertAlmostEqual(r.evidence["objects"]["blue"]["displacement_px"], 10.0, places=3)
        self.assertGreater(
            r.evidence["objects"]["blue"]["displacement_px"], r.evidence["shift_px_threshold"]
        )
        self.assertEqual(r.level(), "medium")

    def test_aspect_flip_is_possible_topple(self):
        """(3) Red 40x10 becomes 10x40 -> possible_topple with aspect evidence."""
        frames = [
            draw([((20, 40, 40, 10), RED)]),
            draw([((20, 40, 40, 10), RED)]),
            draw([((20, 40, 10, 40), RED)]),
        ]
        r = evaluate_rollout(sim(frames), obs(), act("red"))
        self.assertTrue(r.possible_topple)
        ev = r.evidence["topple"]["red"]
        self.assertAlmostEqual(ev["aspect_first"], 4.0, places=3)
        self.assertAlmostEqual(ev["aspect_last"], 0.25, places=3)
        self.assertGreater(ev["aspect_change"], DEFAULT_THRESHOLDS["aspect_change"])
        self.assertAlmostEqual(ev["area_first"], 400)
        self.assertAlmostEqual(ev["area_last"], 400)
        self.assertTrue(any("silhouette" in n for n in r.notes))

    def test_occlusion_from_expected_final(self):
        """(4) Green covers half of red's expected region -> occlusion_risk ~ 0.5."""
        expected = draw([((60, 60, 40, 40), RED)])
        frames = [
            draw([((10, 10, 40, 40), RED)]),
            draw([((10, 10, 40, 40), RED), ((60, 60, 40, 20), GREEN)]),
        ]
        r = evaluate_rollout(sim(frames), obs(), act("red"), expected_final=expected)
        self.assertAlmostEqual(r.occlusion_risk, 0.5, delta=0.1)
        self.assertEqual(r.evidence["occlusion"]["mode"], "expected_final")
        self.assertEqual(r.evidence["occlusion"]["expected_area"], 1600)
        self.assertEqual(r.evidence["occlusion"]["covered_by_others_px"], 800)
        # no next-target info -> accessibility is the announced fallback
        self.assertAlmostEqual(r.accessibility_risk, 0.25, places=5)
        self.assertTrue(any("fallback" in n for n in r.notes))

    def test_accessibility_from_fully_covered_next_target(self):
        """(5) next_target_mask entirely covered -> accessibility_risk ~ 1.0."""
        mask = np.zeros(SIZE, dtype=bool)
        mask[60:80, 60:80] = True
        frames = [
            draw([((10, 10, 20, 20), RED)]),
            draw([((10, 10, 20, 20), RED), ((55, 55, 30, 30), GREEN)]),
        ]
        r = evaluate_rollout(sim(frames), obs(), act("red"), next_target_mask=mask)
        self.assertAlmostEqual(r.accessibility_risk, 1.0, places=5)
        self.assertEqual(r.evidence["accessibility"]["mode"], "next_target_mask")
        self.assertEqual(r.evidence["accessibility"]["region_area"], 400)

    def test_two_frames_lowers_confidence_by_point_two(self):
        """(6) 2 frames vs 6 frames -> confidence 0.2 lower."""
        frames = translating_red(6, 6)
        c6 = evaluate_rollout(sim(frames), obs(), act("red")).confidence
        c2 = evaluate_rollout(sim([frames[0], frames[-1]]), obs(), act("red")).confidence
        self.assertAlmostEqual(c6 - c2, 0.2, places=5)
        self.assertAlmostEqual(c6, 0.8, places=5)

    def test_unsegmentable_acted_object_lowers_confidence(self):
        frames = translating_red(6, 6)
        r = evaluate_rollout(sim(frames), obs(), act("green"))  # green is never drawn
        self.assertAlmostEqual(r.confidence, 0.6, places=5)
        self.assertTrue(any("never confidently segmented" in n for n in r.notes))

    def test_noise_lowers_confidence(self):
        rng = np.random.default_rng(0)
        frames = [
            np.clip(f.astype(np.int32) + rng.integers(-30, 31, f.shape), 0, 255).astype(np.uint8)
            for f in translating_red(6, 6)
        ]
        r = evaluate_rollout(sim(frames), obs(), act("red"))
        self.assertGreater(r.evidence["noise_median_abs_diff"], DEFAULT_THRESHOLDS["noise_median_abs_diff"])
        self.assertAlmostEqual(r.evidence["confidence_terms"]["noisy_frames"], -0.1, places=5)


class TestUnsegmentedRegime(unittest.TestCase):
    def test_low_confidence_and_notes(self):
        """(7) No object_colors -> change-energy only, confidence <= 0.35."""
        frames = [draw([((10 + 8 * i, 50, 20, 20), RED)]) for i in range(6)]
        r = evaluate_rollout(sim(frames), obs(colors=None), act("red"))
        self.assertIsNotNone(r)
        self.assertEqual(r.evidence["regime"], "unsegmented")
        self.assertLessEqual(r.confidence, 0.35)
        self.assertFalse(r.possible_topple)
        self.assertTrue(any("not inferable without segmentation" in n for n in r.notes))
        self.assertTrue(any("UNSEGMENTED regime" in n for n in r.notes))
        self.assertEqual(r.evidence["acted_region_source"], "largest_early_change_blob")
        self.assertAlmostEqual(r.accessibility_risk, 0.5 * r.occlusion_risk, places=5)

    def test_target_region_kwarg_suppresses_shift(self):
        frames = [draw([((10 + 8 * i, 50, 20, 20), RED)]) for i in range(6)]
        r = evaluate_rollout(
            sim(frames), obs(colors=None, target_region_px=(0, 40, 90, 80)), act("red")
        )
        self.assertEqual(r.evidence["acted_region_source"], "target_region_px")
        self.assertFalse(r.visible_shift)  # all change is inside the declared acted region
        self.assertGreater(r.occlusion_risk, 0.0)


class TestGuardsAndOutput(unittest.TestCase):
    def test_failed_result_returns_none(self):
        """(8) status != complete -> None."""
        frames = translating_red(6, 6)
        self.assertIsNone(evaluate_rollout(sim(frames, status="failed"), obs(), act("red")))
        self.assertIsNone(evaluate_rollout(sim(frames, status="unavailable"), obs(), act("red")))
        self.assertIsNone(evaluate_rollout(sim(frames[:1]), obs(), act("red")))
        self.assertIsNone(evaluate_rollout(sim([]), obs(), act("red")))

    def test_compose_and_save_png_roundtrip(self):
        """(10) Grid size is the documented formula; save_png round-trips."""
        frames = translating_red(6, 6)
        grid = compose_side_by_side(
            [("cand-a", frames), ("cand-b", frames)], expected=frames[-1]
        )
        pad, caption_h, cell = 8, 14, 128
        self.assertEqual(
            grid.shape, (pad + 2 * (cell + caption_h + pad), pad + 3 * (cell + pad), 3)
        )
        self.assertEqual(grid.dtype, np.uint8)
        # first frame really is pasted under its caption
        self.assertTrue(np.array_equal(grid[pad + caption_h : pad + caption_h + cell, pad : pad + cell], frames[0]))
        # no expected column -> one column fewer
        self.assertEqual(compose_side_by_side([("a", frames)]).shape[1], pad + 2 * (cell + pad))
        # a banner (used to name the world model) adds a 26px strip and shifts the grid down
        banded = compose_side_by_side([("a", frames)], banner="world model: mock -- synthetic")
        self.assertEqual(banded.shape[0], 26 + pad + (cell + caption_h + pad))
        self.assertTrue(np.array_equal(banded[26 + pad + caption_h : 26 + pad + caption_h + cell, pad : pad + cell], frames[0]))
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "grid.png"
            self.assertEqual(save_png(grid, path), str(path))
            self.assertTrue(np.array_equal(np.asarray(Image.open(path).convert("RGB")), grid))

    def test_determinism(self):
        """(11) Same inputs -> identical signals and evidence."""
        frames = translating_red(6, 6, blue_step=2)
        a = evaluate_rollout(sim(frames), obs(), act("red"), expected_final=frames[-1])
        b = evaluate_rollout(sim(frames), obs(), act("red"), expected_final=frames[-1])
        self.assertEqual(repr(a), repr(b))
        self.assertEqual(a.confidence, b.confidence)
        self.assertEqual(a.evidence["objects"], b.evidence["objects"])

    def test_thresholds_override(self):
        frames = translating_red(6, 6, blue_step=2)
        strict = evaluate_rollout(sim(frames), obs(), act("red"), thresholds={"shift_frac_diag": 0.5})
        self.assertFalse(strict.visible_shift)
        self.assertEqual(strict.evidence["thresholds"]["shift_frac_diag"], 0.5)
        self.assertEqual(DEFAULT_THRESHOLDS["shift_frac_diag"], 0.02)  # module dict untouched


if __name__ == "__main__":
    unittest.main()
