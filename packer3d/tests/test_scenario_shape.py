"""Real suitcase interior shape: wheel wells, handle rails, zones.

Everything here is opt-in; the last test is the one that matters for the other sessions --
a scenario written before any of it loads exactly as it did.
"""
import pytest

from packer3d import Container, Item, pack_naive
from packer3d.scenario import SUITCASE_PROFILES, Zone, load_scenario, load_zones, suitcase_obstacles

CABIN = (0.40, 0.55, 0.23)   # x across the bag, y wheel end -> handle end, z up. IATA 55x40x23.


def test_two_wheel_profile_matches_the_measured_centimetres():
    obs = {o.id: o for o in suitcase_obstacles(CABIN, "two_wheel")}
    assert sorted(obs) == ["handle_rails", "wheel_well_1", "wheel_well_2"]
    dx, dy, dz = obs["wheel_well_1"].dims
    assert (round(dx, 3), round(dy, 3), round(dz, 3)) == (0.06, 0.099, 0.051)   # ~6 x 10 x 5 cm
    assert obs["wheel_well_1"].position == (0.0, 0.0, 0.0)                      # rear floor corners
    assert round(obs["wheel_well_2"].position[0], 3) == 0.34                    # 0.40 - 0.06
    assert obs["wheel_well_2"].position[1:] == (0.0, 0.0)
    rails = obs["handle_rails"]
    assert round(rails.dims[2], 3) == 0.025                                     # 2.5 cm proud
    assert rails.position[2] == 0.0                                             # up from the floor


def test_rails_never_overlap_the_wheel_wells():
    # overlapping obstacles would be double-counted by Container.usable_volume
    for profile in SUITCASE_PROFILES:
        obs = {o.id: o for o in suitcase_obstacles(CABIN, profile)}
        rails, well = obs["handle_rails"], obs["wheel_well_1"]
        assert rails.position[0] >= well.position[0] + well.dims[0] - 1e-9
        assert rails.position[0] + rails.dims[0] <= CABIN[0] - well.dims[0] + 1e-9
        c = Container("c", CABIN, obstacles=list(obs.values()))
        assert c.usable_volume == pytest.approx(c.volume - sum(o.volume for o in obs.values()))


def test_spinner_takes_all_four_floor_corners():
    obs = suitcase_obstacles(CABIN, "spinner")
    wells = [o for o in obs if o.id.startswith("wheel_well")]
    assert len(wells) == 4
    assert sorted((round(o.position[0], 4), round(o.position[1], 4)) for o in wells) == [
        (0.0, 0.0), (0.0, 0.4675), (0.32, 0.0), (0.32, 0.4675)]


def test_fractions_override_and_scale():
    assert suitcase_obstacles(CABIN, "two_wheel", {"handle_rails": None}) == \
        [o for o in suitcase_obstacles(CABIN, "two_wheel") if o.id != "handle_rails"]
    tiny = suitcase_obstacles((0.20, 0.275, 0.115), "two_wheel")   # half-size bag, half-size wells
    assert tiny[0].dims == pytest.approx([d / 2 for d in suitcase_obstacles(CABIN, "two_wheel")[0].dims])
    measured = suitcase_obstacles(CABIN, "two_wheel", {"wheel_well": (0.1, 0.1, 0.1)})[0]
    assert measured.dims == pytest.approx((0.04, 0.055, 0.023))
    with pytest.raises(ValueError, match="unknown suitcase profile"):
        suitcase_obstacles(CABIN, "steamer_trunk")


def test_profile_key_puts_the_obstacles_in_the_container():
    c, _, _, _ = load_scenario({"container": {"dims": list(CABIN), "profile": "two_wheel"}, "items": []})
    assert [o.id for o in c.obstacles] == ["wheel_well_1", "wheel_well_2", "handle_rails"]
    assert c.usable_volume < c.volume
    # and the solver actually keeps out: a block that only fits in a wheel-well corner cannot go there
    corner = Item.box("corner", 0.05, 0.09, 0.04)
    res = pack_naive(c, [corner])
    (x, y, z), (dx, dy, _) = res.placements[0].position, res.placements[0].dims
    assert not (x < 0.06 - 1e-9 and y < 0.099 - 1e-9 and z < 0.051 - 1e-9)
    assert not (x + dx > 0.34 + 1e-9 and y + dy < 1e-9)


def test_lid_pocket_zone_takes_only_the_items_that_ask_for_it():
    zones, _, _ = load_zones({
        "container": {"dims": list(CABIN)},
        "zones": [{"id": "interior", "label": "Interior"},
                  {"id": "lid_pocket", "label": "Lid pocket", "origin": [0, 0, 0.23],
                   "size": [0.40, 0.55, 0.05], "gravity": False, "min_support": 0.0}],
        "items": [{"id": "map", "shape": "box", "dims": [0.2, 0.3, 0.01], "zone": "lid_pocket"},
                  {"id": "boots", "shape": "box", "dims": [0.3, 0.2, 0.15]}],
    })
    (interior, ic, iitems), (lid, lc, litems) = zones
    assert (interior.id, lid.id) == ("interior", "lid_pocket")
    assert lid.origin == (0.0, 0.0, 0.23) and lid.size == (0.40, 0.55, 0.05)
    assert [i.id for i in iitems] == ["boots"] and [i.id for i in litems] == ["map"]
    assert ic.dims == CABIN and lc.dims == (0.40, 0.55, 0.05)
    assert lc.gravity is False and lc.min_support == 0.0 and ic.gravity is True
    # the pocket's own depth is the "flat things only" rule
    assert Item.box("map", 0.2, 0.3, 0.01).fits_in(lc)
    assert not Item.box("boots", 0.3, 0.2, 0.15).fits_in(lc)


def test_zone_clips_the_parent_obstacles():
    zones, _, _ = load_zones({
        "container": {"dims": list(CABIN), "profile": "two_wheel"},
        "zones": [{"id": "floor", "label": "Floor", "origin": [0, 0, 0], "size": [0.40, 0.55, 0.02]},
                  {"id": "lid", "label": "Lid", "origin": [0, 0, 0.23], "size": [0.40, 0.55, 0.05]}],
    })
    (_, floor_c, _), (_, lid_c, _) = zones
    assert {o.id for o in floor_c.obstacles} == {"wheel_well_1", "wheel_well_2", "handle_rails"}
    assert all(o.dims[2] == 0.02 for o in floor_c.obstacles)   # clipped to the slab
    assert lid_c.obstacles == ()                               # nothing intrudes into the lid


def test_zone_errors_are_loud():
    with pytest.raises(ValueError, match="does not declare"):
        load_zones({"container": {"dims": list(CABIN)},
                    "zones": [{"id": "interior"}],
                    "items": [{"id": "map", "shape": "box", "dims": [0.1, 0.1, 0.1], "zone": "lid_pocket"}]})
    with pytest.raises(ValueError, match="duplicate zone id"):
        load_zones({"container": {"dims": list(CABIN)}, "zones": [{"id": "a"}, {"id": "a"}]})
    with pytest.raises(ValueError, match="missing required key 'id'"):
        load_zones({"container": {"dims": list(CABIN)}, "zones": [{"label": "no id"}]})


def test_heightmap_payload_honours_allow_lay_down():
    # regression: the "heights" branch dropped allow_lay_down, so a scanned cylinder that must
    # stay standing still got its two laid-down orientations and the solver could tip it over.
    # (Unreachable from the phone today -- its scans classify as boxes -- so this is the guard.)
    cell, n = 0.01, 12
    disc = [[0.30 if (i - 5.5) ** 2 + (j - 5.5) ** 2 <= 36 else 0.0 for j in range(n)] for i in range(n)]
    scan = {"id": "thermos", "width": n * cell * 100, "depth": n * cell * 100, "height": 30.0,
            "cellSize": cell * 100, "heights": [[h * 100 for h in row] for row in disc]}
    _, (standing,), _, _ = load_scenario({"container": {"dims": list(CABIN)},
                                          "items": [dict(scan, allow_lay_down=False)]})
    _, (tipping,), _, _ = load_scenario({"container": {"dims": list(CABIN)}, "items": [scan]})
    assert standing.shape == "cylinder" and standing.allow_lay_down is False
    assert [o.name for o in standing.orientations()] == ["cyl_axis_z"]
    assert len(tipping.orientations()) == 3


def test_scenarios_written_before_any_of_this_are_untouched():
    old = {"container": {"id": "suitcase", "dims": [0.75, 0.50, 0.28], "max_mass": 23, "min_support": 0.6},
           "items": [{"id": "shoes", "shape": "box", "dims": [0.32, 0.20, 0.12], "mass": 1.2, "count": 2}],
           "optimizer": {"seed": 3}, "weights": {"com": 6}}
    c, items, cfg, w = load_scenario(old)
    assert c.obstacles == () and [i.id for i in items] == ["shoes_1", "shoes_2"]
    assert cfg.seed == 3 and w.com == 6
    # ...and the zone-aware view of the same scenario is one zone holding everything
    (zone, zc, zitems), = load_zones(old)[0]
    assert (zone.id, zone.label) == ("interior", "Interior")
    assert (zone.origin, zone.size) == ((0.0, 0.0, 0.0), c.dims)
    assert zc.dims == c.dims and [i.id for i in zitems] == [i.id for i in items]
    assert isinstance(zone, Zone)
