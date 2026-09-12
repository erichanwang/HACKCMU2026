"""Tests for pan/observation.py (the scene -> PAN observation adapter)."""
from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from pan.evaluate import segment_by_color
from pan.observation import (
    DEFAULT_VIEWPOINTS,
    build_pan_input,
    canonicalize,
    color_for_id,
    observation_from_image,
    observation_from_scene,
    project_points,
    render_scene,
    save_observation,
    viewpoint_for_container,
    viewpoint_for_scene,
)
from pan.types import Observation, PackingAction, SimulationRequest
from physics.geometry import obb_from, obb_vertices
from physics.schema import Scene
from tests.fixtures import valid_packed_scene

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "pan" / "fixtures"


def _scene_with_item_on_the_table(x: float) -> Scene:
    """valid_packed_scene with the shoe moved out of the suitcase onto the table."""
    scene = valid_packed_scene()
    return Scene(
        container=scene.container,
        objects=[replace(o, position=(x, 0.055, 0.10)) if o.id == "shoe" else o for o in scene.objects],
    )


def _move_object(scene: Scene, object_id: str, delta_xyz: tuple[float, float, float]) -> Scene:
    dx, dy, dz = delta_xyz
    objects = []
    for o in scene.objects:
        if o.id == object_id:
            x, y, z = o.position
            objects.append(replace(o, position=(x + dx, y + dy, z + dz)))
        else:
            objects.append(o)
    return Scene(container=scene.container, objects=objects)


class TestRenderDeterminism(unittest.TestCase):
    def test_render_twice_is_byte_identical(self):
        scene = valid_packed_scene()
        vp = DEFAULT_VIEWPOINTS["overhead_45"]
        img1, meta1 = render_scene(scene, vp)
        img2, meta2 = render_scene(scene, vp)
        self.assertEqual(img1.tobytes(), img2.tobytes())
        self.assertEqual(meta1["object_colors"], meta2["object_colors"])
        self.assertEqual(meta1["projected_centroids"], meta2["projected_centroids"])

    def test_output_shape_and_dtype(self):
        scene = valid_packed_scene()
        vp = DEFAULT_VIEWPOINTS["overhead_45"]
        img, _ = render_scene(scene, vp)
        self.assertEqual(img.shape, (vp.height, vp.width, 3))
        self.assertEqual(img.dtype, np.uint8)


class TestSceneFraming(unittest.TestCase):
    """`frame="scene"` (the default) must keep unpacked items on the table in
    shot; `frame="container"` is the old crop that loses them."""

    def test_both_framings_are_deterministic(self):
        scene = _scene_with_item_on_the_table(-0.7)
        for frame in ("scene", "container"):
            for name in ("overhead_45", "front_high"):
                a = observation_from_scene(scene, name, frame=frame)
                b = observation_from_scene(scene, name, frame=frame)
                self.assertEqual(a.image.tobytes(), b.image.tobytes(), f"{frame}/{name}")
                self.assertEqual(a.viewpoint, b.viewpoint)

    def test_unknown_frame_rejected(self):
        with self.assertRaises(ValueError):
            observation_from_scene(valid_packed_scene(), "overhead_45", frame="sideways")

    def test_table_item_is_framed_by_scene_and_cropped_by_container(self):
        scene = _scene_with_item_on_the_table(-0.7)
        for name in ("overhead_45", "front_high"):
            obs = observation_from_scene(scene, name)  # default framing
            x0, y0, x1, y1 = obs.metadata["object_bboxes"]["shoe"]
            self.assertTrue(obs.metadata["object_visible"]["shoe"])
            self.assertGreaterEqual(x0, 0)
            self.assertLess(x1, obs.viewpoint.width)
            area = int(segment_by_color(obs.image, obs.metadata["object_colors"])["shoe"].sum())
            self.assertGreater(area, 1000, f"{name}: table item should be plainly visible")

            cropped = observation_from_scene(scene, name, frame="container")
            cx0, _, cx1, _ = cropped.metadata["object_bboxes"]["shoe"]
            self.assertLess(cx0, 0, f"{name}: container framing should push it off the left edge")
            on_screen = max(0, min(cx1, cropped.viewpoint.width) - max(cx0, 0))
            self.assertLess(on_screen / (cx1 - cx0), 0.15, f"{name}: barely a sliver should remain")

    def test_object_visible_flag_is_false_when_fully_off_screen(self):
        scene = _scene_with_item_on_the_table(-0.95)
        for name in ("overhead_45", "front_high"):
            self.assertFalse(observation_from_scene(scene, name, frame="container").metadata["object_visible"]["shoe"])
            self.assertTrue(observation_from_scene(scene, name).metadata["object_visible"]["shoe"])

    def test_every_object_is_inside_the_frame_under_scene_framing(self):
        scene = _scene_with_item_on_the_table(-0.7)
        for name in ("overhead_45", "front_high"):
            obs = observation_from_scene(scene, name)
            for oid, (x0, y0, x1, y1) in obs.metadata["object_bboxes"].items():
                self.assertTrue(
                    x0 >= 0 and y0 >= 0 and x1 < obs.viewpoint.width and y1 < obs.viewpoint.height,
                    f"{name}/{oid} clipped: {[x0, y0, x1, y1]}",
                )

    def test_bboxes_are_the_projected_obb_vertices(self):
        scene = _scene_with_item_on_the_table(-0.7)
        obs = observation_from_scene(scene)
        for obj in scene.objects:
            uv = project_points(obb_vertices(obb_from(obj)), obs.viewpoint)
            expected = [
                int(np.floor(uv[:, 0].min())), int(np.floor(uv[:, 1].min())),
                int(np.ceil(uv[:, 0].max())), int(np.ceil(uv[:, 1].max())),
            ]
            self.assertEqual(obs.metadata["object_bboxes"][obj.id], expected, obj.id)

    def test_camera_convention_unchanged_under_scene_framing(self):
        """+X still maps to the right of the image (same convention as the
        container framing), with one fixed viewpoint so only the object moves."""
        scene = _scene_with_item_on_the_table(-0.7)
        vp = viewpoint_for_scene(scene, "front_high")
        _, before = render_scene(scene, vp)
        _, after = render_scene(_move_object(scene, "shoe", (0.1, 0.0, 0.0)), vp)
        u0, v0 = before["projected_centroids"]["shoe"]
        u1, v1 = after["projected_centroids"]["shoe"]
        self.assertGreater(u1, u0)
        self.assertAlmostEqual(v1, v0, places=6)

    def test_ground_plane_is_drawn_under_the_table_item(self):
        """The floor grid/table fill covers the whole framed ground, not just the
        container footprint: the pixels below the off-suitcase shoe are table,
        not empty background."""
        scene = _scene_with_item_on_the_table(-0.7)
        obs = observation_from_scene(scene, "overhead_45")
        image = obs.image
        background = np.array(image[0, 0], dtype=int)  # top-left corner: outside the table
        x0, _, x1, y1 = obs.metadata["object_bboxes"]["shoe"]
        strip = image[y1 + 2 : y1 + 8, x0 + 5 : x1 - 5].reshape(-1, 3).astype(int)
        self.assertGreater(strip.shape[0], 0)
        self.assertTrue(
            np.all(np.abs(strip - background).sum(-1) > 10),
            "expected a drawn table surface directly below the item on the table",
        )


class TestProjectPoints(unittest.TestCase):
    def test_matches_renderer_projected_centroids(self):
        scene = _scene_with_item_on_the_table(-0.7)
        vp = viewpoint_for_scene(scene, "overhead_45")
        _, meta = render_scene(scene, vp)
        for obj in scene.objects:
            uv = project_points([obj.position], vp)
            self.assertEqual(uv.shape, (1, 2))
            self.assertAlmostEqual(uv[0, 0], meta["projected_centroids"][obj.id][0], delta=1e-6)
            self.assertAlmostEqual(uv[0, 1], meta["projected_centroids"][obj.id][1], delta=1e-6)

    def test_accepts_the_viewpoint_dict_carried_in_metadata(self):
        scene = valid_packed_scene()
        obs = observation_from_scene(scene)
        from_dict = project_points([scene.objects[0].position], obs.metadata["viewpoint"])
        from_obj = project_points([scene.objects[0].position], obs.viewpoint)
        self.assertTrue(np.allclose(from_dict, from_obj, atol=1e-12))


class TestObjectColors(unittest.TestCase):
    """Every object gets a distinct, stable color, and it is really visible on
    screen -- not just recorded in metadata."""

    def test_distinct_colors_and_visible_pixels(self):
        scene = valid_packed_scene()
        img, meta = render_scene(scene, DEFAULT_VIEWPOINTS["overhead_45"])
        colors = meta["object_colors"]

        # every object in the scene got a color, and no two share one
        self.assertEqual(set(colors), {o.id for o in scene.objects})
        self.assertEqual(len(set(colors.values())), len(colors))

        # floor-level objects with nothing stacked on top and nothing else in
        # front of them from this angle: guaranteed unoccluded, large faces.
        for object_id in ("laptop", "shoe"):
            mask = np.all(img == np.array(colors[object_id]), axis=-1)
            self.assertGreater(
                int(mask.sum()), 500, f"{object_id} should show many exact-color pixels"
            )

        # objects sitting on top of a stack: their top face faces the overhead
        # camera almost head-on and is unoccluded (nothing is stacked on them).
        for object_id in ("camera", "toiletry_bottle"):
            mask = np.all(img == np.array(colors[object_id]), axis=-1)
            self.assertGreater(
                int(mask.sum()), 20, f"{object_id} should show exact-color pixels on top"
            )

    def test_color_for_id_is_deterministic(self):
        self.assertEqual(color_for_id("shoe"), color_for_id("shoe"))


class TestCentroidProjection(unittest.TestCase):
    def test_moving_object_in_world_x_moves_centroid_right(self):
        """front_high's camera sits at world X=0 looking toward -Z with world
        up, so its screen 'right' axis is world +X (derived in the report):
        moving an object by +0.1m in world X must increase its projected u
        (move right in the image), with v (row) unchanged since Y/Z didn't move.
        """
        scene = valid_packed_scene()
        vp = viewpoint_for_container(scene.container, "front_high")
        _, meta_before = render_scene(scene, vp)

        moved = _move_object(scene, "shoe", (0.1, 0.0, 0.0))
        _, meta_after = render_scene(moved, vp)

        u_before, v_before = meta_before["projected_centroids"]["shoe"]
        u_after, v_after = meta_after["projected_centroids"]["shoe"]
        self.assertGreater(u_after, u_before)
        self.assertAlmostEqual(v_after, v_before, places=6)


class TestGhostAndHighlight(unittest.TestCase):
    def test_ghost_render_differs_only_near_ghost(self):
        scene = valid_packed_scene()
        vp = viewpoint_for_container(scene.container, "front_high")
        shoe = next(o for o in scene.objects if o.id == "shoe")
        action = PackingAction(object_id="shoe", target_position=(0.2, shoe.position[1], -0.15))

        plain, _ = render_scene(scene, vp)
        ghosted, _ = render_scene(scene, vp, ghost=("shoe", action))

        diff = np.any(plain != ghosted, axis=-1)
        self.assertGreater(int(diff.sum()), 0, "ghost overlay should change some pixels")
        # localized: differing pixels are only a small slice of the frame
        self.assertLess(diff.sum(), plain.shape[0] * plain.shape[1] * 0.1)

        # ...and specifically clustered around the ghost's projected footprint
        from physics.geometry import obb_from, obb_vertices
        from pan.observation import _project_points

        ghost_obj = replace(shoe, position=action.target_position, rotation=action.target_rotation)
        uv, _ = _project_points(vp, obb_vertices(obb_from(ghost_obj)))
        margin = 15
        u_lo, u_hi = uv[:, 0].min() - margin, uv[:, 0].max() + margin
        v_lo, v_hi = uv[:, 1].min() - margin, uv[:, 1].max() + margin

        ys, xs = np.nonzero(diff)
        self.assertTrue(np.all((xs >= u_lo) & (xs <= u_hi)))
        self.assertTrue(np.all((ys >= v_lo) & (ys <= v_hi)))

    def test_highlight_changes_image(self):
        scene = valid_packed_scene()
        vp = viewpoint_for_container(scene.container, "overhead_45")
        plain, _ = render_scene(scene, vp)
        highlighted, _ = render_scene(scene, vp, highlight="shoe")
        self.assertTrue(np.any(plain != highlighted))


class TestObservationFromImage(unittest.TestCase):
    def test_synthetic_image_letterbox(self):
        scene = valid_packed_scene()
        src = Image.new("RGB", (640, 480), (10, 20, 30))
        obs = observation_from_image(src, scene, scene_id="synthetic")

        self.assertEqual(obs.image.shape, (512, 512, 3))
        self.assertEqual(obs.image.dtype, np.uint8)
        self.assertEqual(obs.source, "ios_rgb")
        self.assertIsNone(obs.viewpoint)
        self.assertEqual(obs.object_ids, [o.id for o in scene.objects])

        lb = obs.metadata["letterbox"]
        self.assertAlmostEqual(lb["scale"], 0.8)
        self.assertEqual(lb["offset"], [0, 64])
        self.assertEqual(lb["resized_size"], [512, 384])
        self.assertEqual(obs.metadata["original_size"], [640, 480])
        self.assertIsNone(obs.metadata["crop"])

        # original center pixel (320, 240) must map to the output's center
        out_x = lb["offset"][0] + 320 * lb["scale"]
        out_y = lb["offset"][1] + 240 * lb["scale"]
        self.assertAlmostEqual(out_x, 256.0)
        self.assertAlmostEqual(out_y, 256.0)

    def test_canonicalize_matches_observation_from_image(self):
        src = Image.new("RGB", (640, 480), (1, 2, 3))
        direct = canonicalize(src, (512, 512))
        self.assertEqual(direct.shape, (512, 512, 3))
        self.assertEqual(direct.dtype, np.uint8)


class TestBuildPanInput(unittest.TestCase):
    def test_returns_simulation_request_with_unchanged_action(self):
        scene = valid_packed_scene()
        action = PackingAction(object_id="shoe", target_position=(0.1, 0.055, 0.1), text="")
        request = build_pan_input(scene, action)

        self.assertIsInstance(request, SimulationRequest)
        self.assertIs(request.action, action)
        self.assertIsInstance(request.observation, Observation)
        self.assertEqual(request.observation.source, "rendered")
        self.assertRegex(request.request_id, r"^[0-9a-f]{12}$")

    def test_uses_given_observation_as_is_in_mode_a(self):
        scene = valid_packed_scene()
        src = Image.new("RGB", (640, 480), (5, 5, 5))
        obs = observation_from_image(src, scene, scene_id="synthetic")
        action = PackingAction(object_id="shoe", target_position=(0.0, 0.0, 0.0))
        request = build_pan_input(scene, action, observation=obs)
        self.assertIs(request.observation, obs)
        self.assertIs(request.action, action)


class TestSaveObservation(unittest.TestCase):
    def test_writes_png_and_sets_path(self):
        import tempfile

        scene = valid_packed_scene()
        obs = observation_from_scene(scene, "overhead_45")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "obs.png"
            save_observation(obs, path)
            self.assertTrue(path.exists())
            self.assertEqual(obs.image_path, str(path))
            with Image.open(path) as im:
                self.assertEqual(im.size, (obs.image.shape[1], obs.image.shape[0]))


class TestRenderFixturesScript(unittest.TestCase):
    def test_index_and_files_exist(self):
        index_path = FIXTURES_DIR / "index.json"
        self.assertTrue(index_path.exists(), "run pan/render_fixtures.py first")
        index = json.loads(index_path.read_text())
        self.assertGreater(len(index), 0)
        for filename, entry in index.items():
            file_path = FIXTURES_DIR / filename
            self.assertTrue(file_path.exists(), f"missing {filename}")
            size = file_path.stat().st_size
            self.assertLess(size, 60 * 1024, f"{filename} is {size} bytes")
            self.assertIn("scene", entry)
            self.assertIn("viewpoint", entry)
            self.assertIn("object_colors", entry)


if __name__ == "__main__":
    unittest.main()
