"""Hand-computed synthetic Scenes for HackCMU 2026 (Travel track) physics tests.

These are DATA factories only -- no validation logic lives here. Every number
below was chosen and verified by hand (see the arithmetic in each comment);
containment/overlap claims were additionally checked with a throwaway script
using plain AABB math (all fixtures below use axis-aligned, i.e. identity or
pure-Y-rotation, objects, so AABB overlap == true OBB overlap), and the one
rotated case (fixture 8) was checked against physics.geometry.obb_from /
obb_vertices directly.

IMPORTANT axis-mapping note (read before touching any number in this file):
schema.py says dimensions = (length, width, height) along local (x, y, z),
and physics/geometry.py's obb_from() literally does
    half_extents = dims / 2         # applied to axes columns [x, y, z] in order
so under an identity rotation, dims[1] -- not dims[2] -- lands on world Y
(up). The prompt's own container example ("dimensions=(0.56, 0.36, 0.23)",
"center Y = height/2 = 0.115") only works out if the *vertical* extent
(0.23 m) sits at tuple index 1. So every dims tuple in this file is written
as (x_extent, y_extent_i.e._vertical_height, z_extent), reordering the
colloquial "length x width x height" numbers from the spec so the middle
value is always the vertical one. This is called out again in the report;
it is a deliberate, necessary reordering, not a typo.

Container: "carry_on", dims=(0.56, 0.23, 0.36) -> 0.56 m long (X), 0.23 m
tall (Y, the packing depth from the case's floor to its zip line), 0.36 m
deep (Z). position=(0, 0.115, 0) so the floor sits at world Y=0 and the top
opening is at Y=0.23. Identity rotation.
"""
from __future__ import annotations

import math
from dataclasses import replace

from physics.schema import Constraints, Container, Object, Scene

# ---------------------------------------------------------------------------
# container + shared half-extents
# ---------------------------------------------------------------------------

CONTAINER_DIMS = (0.56, 0.23, 0.36)  # (X, Y-up, Z)
CXH, CYH_TOP, CZH = 0.28, 0.23, 0.18  # half-X, full ceiling Y, half-Z


def _container() -> Container:
    return Container(id="carry_on", dimensions=CONTAINER_DIMS, position=(0.0, 0.115, 0.0))


def _quat_y(deg: float) -> tuple[float, float, float, float]:
    """Unit quaternion for a rotation of `deg` about the world Y (up) axis."""
    half = math.radians(deg) / 2.0
    return (0.0, math.sin(half), 0.0, math.cos(half))


LAPTOP_CONSTRAINTS = Constraints(fragile=True, cannot_support_weight=True, orientation_lock="flat_only")
BOTTLE_CONSTRAINTS = Constraints(keep_upright=True)


# ---------------------------------------------------------------------------
# 1. valid_packed_scene -- everything passes
# ---------------------------------------------------------------------------
# Floor layout (X right / Z forward, container floor is X in [-0.28,0.28],
# Z in [-0.18,0.18]):
#   Row A (z in [-0.175,0.035], back of case): laptop (flat, full 0.30x0.21
#     footprint) on the left, headphones case on the right, with charger and
#     the liquid bottle stacked flush on top of the headphones case.
#   Row B (z in [0.04,0.175], front of case): shoe on the left, toiletry bag
#     on the right, with the camera stacked flush on top of the toiletry bag.
# All gaps between rows/items are >=0.005m (no touching except where a stack
# is deliberately flush). Verified: all 7 objects contained, zero pairwise
# AABB overlaps, every stacked item's footprint fully or centered within its
# support and flush in Y.
def _base_objects() -> list[Object]:
    return [
        Object(
            id="laptop",
            dimensions=(0.30, 0.02, 0.21),
            position=(-0.125, 0.01, -0.07),
            mass_kg=1.3,
            constraints=LAPTOP_CONSTRAINTS,
        ),
        Object(
            id="headphones_case",
            dimensions=(0.18, 0.07, 0.18),
            position=(0.14, 0.035, -0.07),
            mass_kg=0.3,
        ),
        # flush on top of headphones_case: top of case = 0.035+0.035 = 0.07
        Object(
            id="charger",
            dimensions=(0.09, 0.03, 0.06),
            position=(0.10, 0.085, -0.10),
            mass_kg=0.1,
        ),
        # flush on top of headphones_case too, beside the charger
        Object(
            id="toiletry_bottle",
            dimensions=(0.06, 0.15, 0.06),
            position=(0.18, 0.145, -0.02),
            mass_kg=0.2,
            constraints=BOTTLE_CONSTRAINTS,
        ),
        Object(
            id="shoe",
            dimensions=(0.29, 0.11, 0.12),
            position=(-0.13, 0.055, 0.10),
            mass_kg=0.3,
        ),
        # toiletry z-extent trimmed 0.15 -> 0.135 so it plus the 0.005 gaps
        # fits the 0.145m-deep front row left after the laptop's row; still
        # within the spec's "~0.15" tolerance.
        Object(
            id="toiletry_bag",
            dimensions=(0.20, 0.08, 0.135),
            position=(0.125, 0.04, 0.1075),
            mass_kg=0.5,
        ),
        # flush on top of toiletry_bag, fully within its footprint:
        # top of bag = 0.04+0.04 = 0.08; camera half extents (0.065,0.045,0.05)
        # fit inside the bag's half extents (0.10,0.04,0.0675) in both X and Z.
        Object(
            id="camera",
            dimensions=(0.13, 0.09, 0.10),
            position=(0.125, 0.125, 0.1075),
            mass_kg=0.4,
        ),
    ]


def valid_packed_scene() -> Scene:
    return Scene(container=_container(), objects=_base_objects())


# ---------------------------------------------------------------------------
# 2. scene_with_collision
# ---------------------------------------------------------------------------
def scene_with_collision() -> Scene:
    """Same 7 items as valid_packed_scene, but shoe is shifted -0.025m in Z
    so it overlaps laptop by exactly 0.02m along Z.

    laptop z-range = [-0.175, 0.035] (center -0.07, half 0.105, unchanged).
    shoe z-range (new) = [0.015, 0.135] (center 0.075, half 0.06):
        overlap_z = min(0.035, 0.135) - max(-0.175, 0.015) = 0.035 - 0.015 = 0.02m.
    (X and Y also happen to overlap fully here since shoe's X sits entirely
    inside laptop's X range and both rest on the floor -- only the Z overlap
    is the deliberately engineered amount.)
    """
    objects = _base_objects()
    objects = [
        replace(o, position=(-0.13, 0.055, 0.075)) if o.id == "shoe" else o for o in objects
    ]
    return Scene(container=_container(), objects=objects)


# ---------------------------------------------------------------------------
# 3. scene_with_wall_penetration
# ---------------------------------------------------------------------------
def scene_with_wall_penetration() -> Scene:
    """laptop pushed through the -X wall by exactly 0.03m; shoe stays valid.

    laptop half-X = 0.15, center_x = -0.16 -> x_lo = -0.31.
    Container -X wall is at -0.28, so penetration depth = -0.28 - (-0.31) = 0.03m.
    shoe (center=(-0.13,0.055,0.10)) occupies z=[0.04,0.16], which never
    overlaps laptop's z=[-0.175,0.035] regardless of laptop's X, so shoe
    stays contained and non-overlapping.
    """
    laptop = Object(
        id="laptop",
        dimensions=(0.30, 0.02, 0.21),
        position=(-0.16, 0.01, -0.07),
        mass_kg=1.3,
        constraints=LAPTOP_CONSTRAINTS,
    )
    shoe = Object(
        id="shoe",
        dimensions=(0.29, 0.11, 0.12),
        position=(-0.13, 0.055, 0.10),
        mass_kg=0.3,
    )
    return Scene(container=_container(), objects=[laptop, shoe])


# ---------------------------------------------------------------------------
# 4. scene_with_floating_object
# ---------------------------------------------------------------------------
def scene_with_floating_object() -> Scene:
    """charger floats 0.05m above the floor with nothing under it.

    laptop (x=[-0.275,0.025], z=[-0.175,0.035]) and shoe (x=[-0.275,0.015],
    z=[0.04,0.16]) are both valid and occupy only x <= 0.025, so the column
    at x in [0.105,0.195], z in [-0.03,0.03] is empty floor-to-ceiling.
    charger resting flush would sit at y = half_y = 0.015; here it's placed
    at y = 0.065, i.e. 0.065 - 0.015 = 0.05m above where it would rest on
    the (empty) floor, with no object beneath it.
    """
    laptop = Object(
        id="laptop",
        dimensions=(0.30, 0.02, 0.21),
        position=(-0.125, 0.01, -0.07),
        mass_kg=1.3,
        constraints=LAPTOP_CONSTRAINTS,
    )
    shoe = Object(
        id="shoe",
        dimensions=(0.29, 0.11, 0.12),
        position=(-0.13, 0.055, 0.10),
        mass_kg=0.3,
    )
    charger = Object(
        id="charger",
        dimensions=(0.09, 0.03, 0.06),
        position=(0.15, 0.065, 0.0),
        mass_kg=0.1,
    )
    return Scene(container=_container(), objects=[laptop, shoe, charger])


# ---------------------------------------------------------------------------
# 5. scene_with_precarious_balance
# ---------------------------------------------------------------------------
def scene_with_precarious_balance() -> Scene:
    """camera balanced on the +X edge of toiletry_bag; its center of mass
    projects 0.02m beyond the support footprint.

    toiletry_bag: center=(0,0.04,0), half-extents=(0.10,0.04,0.075) ->
        x-range=[-0.10,0.10], top y = 0.08.
    camera: half-extents=(0.065,0.045,0.05), center=(0.12,0.125,0.0) ->
        x-range=[0.055,0.185] (bottom flush at y=0.08, z fully inside the
        bag's z-range so only X is at issue).
    Contact overlap in X = min(0.10,0.185) - max(-0.10,0.055) = 0.045m > 0,
    so the camera really is touching/resting on the bag -- but its center
    (x=0.12) is 0.12 - 0.10 = 0.02m past the bag's +X edge, i.e. the CoM
    projects outside the support base by 0.02m: a classic tip-over case.
    """
    toiletry = Object(
        id="toiletry_bag",
        dimensions=(0.20, 0.08, 0.15),
        position=(0.0, 0.04, 0.0),
        mass_kg=0.5,
    )
    camera = Object(
        id="camera",
        dimensions=(0.13, 0.09, 0.10),
        position=(0.12, 0.125, 0.0),
        mass_kg=0.4,
    )
    return Scene(container=_container(), objects=[toiletry, camera])


# ---------------------------------------------------------------------------
# 6. scene_nearly_full
# ---------------------------------------------------------------------------
def scene_nearly_full() -> Scene:
    """All 7 items packed with ~0.001m gaps everywhere (vs. ~0.005-0.02m in
    valid_packed_scene) -- floor footprint is almost entirely used, nothing
    overlaps. Same row layout as valid_packed_scene: laptop+headphones_case
    (with charger+toiletry_bottle stacked on the case) in the back row,
    shoe+toiletry_bag (with camera stacked on the bag) in the front row.
    Verified by script: all 7 contained, zero pairwise AABB overlaps,
    total item volume / container volume ~= 0.25 (container is shallow, so
    volume fraction looks low even though the floor is essentially full --
    it's floor-footprint slack that's small here, not headroom).
    """
    objects = [
        Object(
            id="laptop",
            dimensions=(0.30, 0.02, 0.21),
            position=(-0.129, 0.01, -0.074),
            mass_kg=1.3,
            constraints=LAPTOP_CONSTRAINTS,
        ),
        Object(
            id="headphones_case",
            dimensions=(0.18, 0.07, 0.18),
            position=(0.189, 0.035, -0.074),
            mass_kg=0.3,
        ),
        Object(
            id="charger",
            dimensions=(0.09, 0.03, 0.06),
            position=(0.145, 0.085, -0.133),
            mass_kg=0.1,
        ),
        Object(
            id="toiletry_bottle",
            dimensions=(0.06, 0.15, 0.06),
            position=(0.221, 0.145, -0.133),
            mass_kg=0.2,
            constraints=BOTTLE_CONSTRAINTS,
        ),
        Object(
            id="shoe",
            dimensions=(0.29, 0.11, 0.12),
            position=(-0.134, 0.055, 0.092),
            mass_kg=0.3,
        ),
        Object(
            id="toiletry_bag",
            dimensions=(0.20, 0.08, 0.145),
            position=(0.112, 0.04, 0.1045),
            mass_kg=0.5,
        ),
        Object(
            id="camera",
            dimensions=(0.13, 0.09, 0.10),
            position=(0.112, 0.125, 0.1045),
            mass_kg=0.4,
        ),
    ]
    return Scene(container=_container(), objects=objects)


# ---------------------------------------------------------------------------
# 7. scene_object_touching_wall
# ---------------------------------------------------------------------------
def scene_object_touching_wall() -> Scene:
    """laptop's -X face sits exactly flush against the container's -X wall
    (valid: touching, not penetrating); shoe is placed normally alongside.

    laptop half-X = 0.15, center_x = -0.13 -> x_lo = -0.13 - 0.15 = -0.28,
    which equals the container's -X wall (-CXH = -0.28) exactly.
    """
    laptop = Object(
        id="laptop",
        dimensions=(0.30, 0.02, 0.21),
        position=(-0.13, 0.01, -0.07),
        mass_kg=1.3,
        constraints=LAPTOP_CONSTRAINTS,
    )
    shoe = Object(
        id="shoe",
        dimensions=(0.29, 0.11, 0.12),
        position=(-0.13, 0.055, 0.10),
        mass_kg=0.3,
    )
    return Scene(container=_container(), objects=[laptop, shoe])


# ---------------------------------------------------------------------------
# 8. scene_rotated_object_in_corner
# ---------------------------------------------------------------------------
def scene_rotated_object_in_corner() -> Scene:
    """shoe rotated 45 degrees about Y and tucked into the (-X,-Z) corner.

    Verified with physics.geometry.obb_from/obb_vertices (not just hand
    computation): for shoe dims=(0.29,0.11,0.12) at position
    (-0.13, 0.055, -0.03) with rotation quat_y(45deg), the actual rotated
    OBB's world vertices span x in [-0.27496, 0.01496] and z in
    [-0.17496, 0.11496]. The container's walls are at x=-0.28/+0.28 and
    z=-0.18/+0.18, so this leaves ~0.005m clearance on both corner walls
    and the object is fully contained.
    toiletry_bag is placed on the opposite side (x in [0.05,0.25], z in
    [0.025,0.175]) -- its AABB doesn't overlap the shoe's AABB
    (shoe x_max=0.01496 < toiletry x_min=0.05), so no collision.
    """
    shoe = Object(
        id="shoe",
        dimensions=(0.29, 0.11, 0.12),
        position=(-0.13, 0.055, -0.03),
        rotation=_quat_y(45.0),
        mass_kg=0.3,
    )
    toiletry = Object(
        id="toiletry_bag",
        dimensions=(0.20, 0.08, 0.15),
        position=(0.15, 0.04, 0.10),
        mass_kg=0.5,
    )
    return Scene(container=_container(), objects=[shoe, toiletry])


# ---------------------------------------------------------------------------
# 9. scene_stacked_objects
# ---------------------------------------------------------------------------
def scene_stacked_objects() -> Scene:
    """headphones_case stacked flush on top of toiletry_bag.

    toiletry_bag: center=(0,0.04,0), half-extents=(0.10,0.04,0.075) -> top y=0.08.
    headphones_case: half-extents=(0.09,0.035,0.09), center=(0,0.115,0) ->
        bottom y = 0.115-0.035 = 0.08, flush with the bag's top.
    XZ footprints: headphones_case x-range=[-0.09,0.09] is fully inside the
    bag's x-range=[-0.10,0.10] (0.01m margin each side); in Z the case's
    range=[-0.09,0.09] slightly exceeds the bag's [-0.075,0.075] by 0.015m
    on each side, but since the case is centered directly above the bag its
    center of mass still projects onto the bag's footprint -- a stable,
    if slightly overhanging, stack.
    """
    toiletry = Object(
        id="toiletry_bag",
        dimensions=(0.20, 0.08, 0.15),
        position=(0.0, 0.04, 0.0),
        mass_kg=0.5,
    )
    headphones = Object(
        id="headphones_case",
        dimensions=(0.18, 0.07, 0.18),
        position=(0.0, 0.115, 0.0),
        mass_kg=0.3,
    )
    return Scene(container=_container(), objects=[toiletry, headphones])


# ---------------------------------------------------------------------------
# 10. scene_oversized_object
# ---------------------------------------------------------------------------
def scene_oversized_object() -> Scene:
    """A fake item 0.70m long -- bigger than the container's 0.56m X extent
    -- so it can never fit regardless of position or rotation.
    """
    oversized = Object(
        id="oversized_duffel",
        dimensions=(0.70, 0.05, 0.20),
        position=(0.0, 0.025, 0.0),
        mass_kg=2.0,
    )
    return Scene(container=_container(), objects=[oversized])


# ---------------------------------------------------------------------------
# 11. scene_with_soft_item_compression
# ---------------------------------------------------------------------------
def scene_with_soft_item_compression() -> Scene:
    """A soft clothes bag (rigidity="soft", k=2.0) overlapping a rigid
    toiletry bag by 0.05m along X -- within the soft item's compression
    allowance, so validate_layout should report SOFT_COMPRESSION (a warning),
    not a hard OBJECT_COLLISION violation. Both objects rest flush on the
    floor and are fully contained.

    clothes_bag: half-extents=(0.10,0.05,0.075), center=(-0.075,0.05,0.0) ->
        x-range=[-0.175,0.025], y-range=[0,0.10], z-range=[-0.075,0.075].
    toiletry_bag: half-extents=(0.10,0.04,0.075), center=(0.075,0.04,0.0) ->
        x-range=[-0.025,0.175], y-range=[0,0.08], z-range=[-0.075,0.075].
    Overlap: x = min(0.025,0.175)-max(-0.175,-0.025) = 0.05m (the SAT
    minimum -- y overlap is 0.08m, z overlap is the full 0.15m, both larger).
    Soft-item allowance along X = extent(0.20) * (1 - 1/k=2.0) * fraction(soft=1.0)
        = 0.20 * 0.5 * 1.0 = 0.10m > 0.05m raw overlap -> fully absorbed.
    """
    clothes_bag = Object(
        id="clothes_bag",
        dimensions=(0.20, 0.10, 0.15),
        position=(-0.075, 0.05, 0.0),
        mass_kg=0.4,
        rigidity="soft",
        compressibility_k=2.0,
    )
    toiletry_bag = Object(
        id="toiletry_bag",
        dimensions=(0.20, 0.08, 0.15),
        position=(0.075, 0.04, 0.0),
        mass_kg=0.5,
    )
    return Scene(container=_container(), objects=[clothes_bag, toiletry_bag])


# ---------------------------------------------------------------------------
# LiDAR scanned footprints (convex polygons in local x/z, see physics/io.py's
# object_from_box_fit / object_from_scanned_item and physics/schema.py's
# Object.footprint docstring). All three shapes below were checked with
# tests/scratch (physics.geometry.convex_hull_2d + polygon_area_2d): every
# listed point survives as a hull vertex (none is interior -- confirming the
# shape really is a hexagon/octagon/pentagon, not fewer effective points),
# every point lies within the object's dims/2 bounding rectangle, and the
# polygon has positive area. See docs/LIDAR_HULLS.md for the area numbers.
#
# shoe: dims=(0.29, 0.11, 0.12) -> half-extents (hx=0.145, hz=0.06). A tapered
# hexagon: narrow end (toe, z=+-0.02) at local x=-0.145, wide end (heel,
# z=+-0.05) at local x=+0.145, with the widest point (ball of foot,
# z=+-0.06, touching the box's z-limit exactly) at x=0. Area = 0.027550 m^2
# vs the box's 0.145*2 * 0.06*2 = 0.034800 m^2 (79.2% of the box).
SHOE_FOOTPRINT = [
    (-0.145, -0.02),
    (0.0, -0.06),
    (0.145, -0.05),
    (0.145, 0.05),
    (0.0, 0.06),
    (-0.145, 0.02),
]
# toiletry_bag: dims=(0.20, 0.08, 0.135) -> half-extents (hx=0.10, hz=0.0675).
# A rounded octagon: the four corners of the 0.20x0.135 rectangle are each
# chamfered by 0.02m in x and 0.02m in z. Area = 0.026200 m^2 vs the box's
# 0.10*2 * 0.0675*2 = 0.027000 m^2 (97.0% of the box).
TOILETRY_BAG_FOOTPRINT = [
    (0.08, -0.0675),
    (0.10, -0.0475),
    (0.10, 0.0475),
    (0.08, 0.0675),
    (-0.08, 0.0675),
    (-0.10, 0.0475),
    (-0.10, -0.0475),
    (-0.08, -0.0675),
]
# camera: dims=(0.13, 0.09, 0.10) -> half-extents (hx=0.065, hz=0.05). The
# 0.13x0.10 rectangle with its (+x, +z) corner chamfered (the grip) from
# (0.065, 0.05) down to two points (0.065, 0.02) and (0.035, 0.05). Area =
# 0.012550 m^2 vs the box's 0.065*2 * 0.05*2 = 0.013000 m^2 (96.5%).
CAMERA_FOOTPRINT = [
    (0.065, -0.05),
    (0.065, 0.02),
    (0.035, 0.05),
    (-0.065, 0.05),
    (-0.065, -0.05),
]


# ---------------------------------------------------------------------------
# 12. scene_scanned_hulls
# ---------------------------------------------------------------------------
def scene_scanned_hulls() -> Scene:
    """`valid_packed_scene`, but `shoe`, `toiletry_bag` and `camera` carry the
    scanned footprints above instead of a plain box. Same 7 objects, same
    poses as `valid_packed_scene` -- a convex prism is always inside its box
    (`footprint_local` enforces every point within +-dims/2), so nothing that
    was non-overlapping/contained/supported by the box geometry can newly
    collide, escape the container, or lose support by shrinking to the
    footprint; `validate_layout` on this scene is still `valid`.
    """
    objects = _base_objects()
    footprints = {
        "shoe": SHOE_FOOTPRINT,
        "toiletry_bag": TOILETRY_BAG_FOOTPRINT,
        "camera": CAMERA_FOOTPRINT,
    }
    objects = [
        replace(o, footprint=footprints[o.id]) if o.id in footprints else o for o in objects
    ]
    return Scene(container=_container(), objects=objects)


# ---------------------------------------------------------------------------
# 13. scene_hull_clears_box_would_not
# ---------------------------------------------------------------------------
def scene_hull_clears_box_would_not() -> Scene:
    """`camera` and `shoe`, both resting on the floor, positioned so their
    BOXES overlap by ~1cm x 1cm at a corner but their scanned-hull FOOTPRINTS
    do not touch -- the shoe's toe (narrow end, local x=-0.145) sits in the
    camera's chamfered grip corner (local +x,+z), and the chamfer/taper carve
    exactly that corner away from both hulls.

    camera: half-extents (hx=0.065, hz=0.05), center=(-0.14, 0.045, -0.05) ->
        box x=[-0.205,-0.075], z=[-0.10, 0.0].
    shoe: half-extents (hx=0.145, hz=0.06), center=(0.06, 0.055, 0.05) ->
        box x=[-0.085, 0.205], z=[-0.01, 0.11].
    Box overlap: overlap_x = min(camera_x_max, shoe_x_max) -
        max(camera_x_min, shoe_x_min) = min(-0.075, 0.205) - max(-0.205,
        -0.085) = -0.075 - (-0.085) = 0.01m; overlap_z = min(camera_z_max,
        shoe_z_max) - max(camera_z_min, shoe_z_min) = min(0.0, 0.11) -
        max(-0.10, -0.01) = 0.0 - (-0.01) = 0.01m -- a 1cm x 1cm corner
        square, x in [-0.085,-0.075], z in [-0.01, 0.0].
    In camera-local coords that square is x in [0.055,0.065], z in
        [0.04,0.05] -- inside the chamfered-away corner (the chamfer edge
        runs from (0.065,0.02) to (0.035,0.05), i.e. local x+z=0.085; the
        square's nearest corner (0.055,0.04) has x+z=0.095 > 0.085, so it's
        outside the hull with margin). In shoe-local coords the same square
        is x in [-0.145,-0.135], z in [-0.06,-0.05] -- at the toe tip
        (x=-0.145) the hull only spans z in [-0.02,0.02], well short of
        z=-0.05, so the hull is nowhere near this corner either.
    Verified with a throwaway separating-axis check (both AABB overlap and
    hull disjointness) before writing this docstring; the same check is
    re-run as a test in tests/test_fixtures.py. Whether `validate_layout`
    itself reports this pair as clear depends on the collision module's
    (concurrent) footprint support -- that assertion belongs to the
    collision tests, not here.
    """
    camera = Object(
        id="camera",
        dimensions=(0.13, 0.09, 0.10),
        position=(-0.14, 0.045, -0.05),
        mass_kg=0.4,
        footprint=CAMERA_FOOTPRINT,
    )
    shoe = Object(
        id="shoe",
        dimensions=(0.29, 0.11, 0.12),
        position=(0.06, 0.055, 0.05),
        mass_kg=0.3,
        footprint=SHOE_FOOTPRINT,
    )
    return Scene(container=_container(), objects=[camera, shoe])


# ---------------------------------------------------------------------------
# 14. scene_scanned_footprint_out_of_dims
# ---------------------------------------------------------------------------
def scene_scanned_footprint_out_of_dims() -> Scene:
    """`valid_packed_scene`, but `shoe`'s footprint has a point at local
    x=0.20 -- its half-width is dims[0]/2 = 0.145, so this overshoots by
    0.055m, far past `footprint_local`'s 1e-6 slack. `precompute` (and so
    `validate_layout`) must raise/report MALFORMED_GEOMETRY naming "shoe",
    not raise an uncaught exception.
    """
    objects = _base_objects()
    bad_footprint = [(-0.10, -0.05), (0.20, 0.0), (-0.10, 0.05)]
    objects = [replace(o, footprint=bad_footprint) if o.id == "shoe" else o for o in objects]
    return Scene(container=_container(), objects=objects)
