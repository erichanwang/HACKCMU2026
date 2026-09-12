"""A nested guest's box lies inside its host's, so summing bounding boxes counts it twice.

The app hit this first and fixed it in ``packing-core`` (``PackingPlan.packedVolumeFraction``
and ``PlanStats.fillFraction`` both subtract ``nestedOverlapVolume()``).  These tests pin the
same definition on this side -- ``models.honoured_nesting`` / ``nested_overlap_by_host`` /
``packed_bbox_volume`` -- against the SAME bowl-and-cup fixture the Swift measured
(``packing-core/Sources/PackingPlan/Resources/nested-plan.json``), so the 13.46% -> 12.14%
correction is one number on both sides rather than two that happen to be close.

They also pin the consequence in ``bounds.gap_report``, which was the only place on this side
that summed bounding boxes: a host was charged for the cavity air its own guest was filling,
so the gap it reported exceeded the wasted volume it was meant to explain.

Every figure below is computed from the geometry in the comment above it, never read out of
the implementation.
"""
import unittest

from packer3d import Container, Item, pack_naive
from packer3d.bounds import gap_report, waste_bounds
from packer3d.models import (Placement, honoured_nesting, nested_overlap_by_host,
                             packed_bbox_volume)


def _p(item_id: str, position, dims, nested_in=None, volume=None) -> Placement:
    """A placement carrying only what the volume accounting reads."""
    box = dims[0] * dims[1] * dims[2]
    return Placement(item_id=item_id, shape="box", position=tuple(position), dims=tuple(dims),
                     center=tuple(position[k] + dims[k] / 2.0 for k in range(3)),
                     orientation="xyz", axis=None, radius=None, height=None, mass=0.0,
                     fragile=False, volume=box if volume is None else volume,
                     nested_in=None if nested_in is None
                     else {"item_id": nested_in, "cavity": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]})


def _bowl() -> Item:
    """20 x 20 x 10 cm scan: a 5 cm rim all round a 10 x 10 cm well only 1 cm deep.

    Heightmap integral = (12 rim cells x 10 cm + 4 well cells x 1 cm) x (5 cm)^2
                       = 124 cm x 25 cm^2 = 3100 cm^3 = 0.0031 m^3,
    against a 0.2 x 0.2 x 0.1 = 0.004 m^3 bounding box.  The 0.0009 m^3 difference is the
    well: 0.10 x 0.10 x 0.09 m.
    """
    grid = [[10, 10, 10, 10],
            [10, 1, 1, 10],
            [10, 1, 1, 10],
            [10, 10, 10, 10]]
    data = {"id": "bowl", "width": 20.0, "depth": 20.0, "height": 10.0, "cellSize": 5.0,
            "heights": grid}
    return Item.from_scanned_heightmap(data, mass=0.4, fragile=False)


class SwiftFixtureAgreementTest(unittest.TestCase):
    """The bundled bowl-and-cup plan, placement for placement, in the app's own bag frame.

    bowl  X 0.05-0.25, Y 0.00-0.10, Z 0.05-0.25  ->  0.20 x 0.10 x 0.20 = 0.004     m^3
    cup   X 0.10-0.18, Y 0.03-0.12, Z 0.10-0.18  ->  0.08 x 0.09 x 0.08 = 0.000576  m^3
    shared X 0.10-0.18 (0.08), Y 0.03-0.10 (0.07), Z 0.10-0.18 (0.08) = 0.000448 m^3
    interior 0.34 x 0.20 x 0.50 = 0.034 m^3
    """
    INTERIOR = 0.34 * 0.20 * 0.50

    def setUp(self):
        self.plan = [_p("bowl", (0.05, 0.00, 0.05), (0.20, 0.10, 0.20)),
                     _p("cup", (0.10, 0.03, 0.10), (0.08, 0.09, 0.08), nested_in="bowl")]

    def test_the_shared_volume_is_the_box_intersection(self):
        self.assertEqual(honoured_nesting(self.plan), {"cup": "bowl"})
        self.assertAlmostEqual(nested_overlap_by_host(self.plan)["bowl"],
                               0.08 * 0.07 * 0.08, places=12)

    def test_the_double_counted_and_the_corrected_fill_are_the_swift_numbers(self):
        raw = 0.004 + 0.000576                      # what a plain sum of the boxes claims
        union = raw - 0.000448                      # each cubic metre once
        self.assertAlmostEqual(packed_bbox_volume(self.plan), union, places=12)
        # 0.004576 / 0.034 = 13.4588%, 0.004128 / 0.034 = 12.1412% -- the app's own
        # "13.46% against 12.14%", which is what this side now has to reproduce.
        self.assertAlmostEqual(raw / self.INTERIOR, 0.13458823529411764, places=12)
        self.assertAlmostEqual(packed_bbox_volume(self.plan) / self.INTERIOR,
                               0.12141176470588235, places=12)

    def test_an_unnested_plan_is_the_plain_sum(self):
        loose = [_p("bowl", (0.05, 0.00, 0.05), (0.20, 0.10, 0.20)),
                 _p("cup", (0.10, 0.12, 0.10), (0.08, 0.09, 0.08))]
        self.assertEqual(nested_overlap_by_host(loose), {})
        self.assertAlmostEqual(packed_bbox_volume(loose), 0.004576, places=12)


class HonouredNestingTest(unittest.TestCase):
    """``nested_in`` is the producer's claim; a broken claim buys no discount."""

    def test_a_host_the_plan_does_not_contain_is_ignored(self):
        plan = [_p("cup", (0.0, 0.0, 0.0), (0.1, 0.1, 0.1), nested_in="ghost")]
        self.assertEqual(honoured_nesting(plan), {})
        self.assertAlmostEqual(packed_bbox_volume(plan), 0.001, places=12)

    def test_a_self_reference_is_ignored(self):
        plan = [_p("cup", (0.0, 0.0, 0.0), (0.1, 0.1, 0.1), nested_in="cup")]
        self.assertEqual(honoured_nesting(plan), {})
        self.assertAlmostEqual(packed_bbox_volume(plan), 0.001, places=12)

    def test_a_cycle_of_hosts_is_ignored(self):
        # a in b, b in a: two identical boxes would otherwise discount 2 x 0.001 off a
        # 0.002 sum and report an empty bag.
        plan = [_p("a", (0.0, 0.0, 0.0), (0.1, 0.1, 0.1), nested_in="b"),
                _p("b", (0.0, 0.0, 0.0), (0.1, 0.1, 0.1), nested_in="a")]
        self.assertEqual(honoured_nesting(plan), {})
        self.assertAlmostEqual(packed_bbox_volume(plan), 0.002, places=12)

    def test_two_guests_in_one_host_are_both_charged_to_it(self):
        # host 0..0.2 cubed = 0.008; guest a 0.02..0.06 cubed = 0.04^3 = 0.000064,
        # guest b 0.10..0.18 cubed = 0.08^3 = 0.000512; both wholly inside the host.
        plan = [_p("host", (0.0, 0.0, 0.0), (0.2, 0.2, 0.2)),
                _p("a", (0.02, 0.02, 0.02), (0.04, 0.04, 0.04), nested_in="host"),
                _p("b", (0.10, 0.10, 0.10), (0.08, 0.08, 0.08), nested_in="host")]
        self.assertAlmostEqual(nested_overlap_by_host(plan)["host"], 0.000064 + 0.000512,
                               places=12)
        self.assertAlmostEqual(packed_bbox_volume(plan), 0.008, places=12)

    def test_a_guest_partly_outside_its_host_shares_only_the_intersection(self):
        # host X/Y/Z 0..0.10; guest X 0.06..0.14, Y/Z 0..0.08 -> shared 0.04 x 0.08 x 0.08.
        plan = [_p("host", (0.0, 0.0, 0.0), (0.10, 0.10, 0.10)),
                _p("guest", (0.06, 0.0, 0.0), (0.08, 0.08, 0.08), nested_in="host")]
        self.assertAlmostEqual(nested_overlap_by_host(plan)["host"], 0.04 * 0.08 * 0.08,
                               places=12)


class GapReportAccountingTest(unittest.TestCase):
    """The cup-in-bowl solve, with the gap arithmetic done by hand.

    container 0.20 x 0.20 x 0.101 -> usable 0.00404 m^3, no obstacles
    bowl true volume 0.0031, cup 0.08 x 0.08 x 0.06 = 0.000384 solid box
    packed (true) volume       0.0031 + 0.000384 = 0.003484
    wasted = 0.00404 - 0.003484                  = 0.000556
      of which: the well the cup does not fill   = 0.0009 - 0.000384 = 0.000516
                headroom above the bowl's rim    = 0.20 x 0.20 x 0.001 = 0.00004
    """
    USABLE = 0.20 * 0.20 * 0.101
    WASTED = 0.000556
    WELL_AIR = 0.0009 - 0.000384
    HEADROOM = 0.20 * 0.20 * 0.001

    def setUp(self):
        self.bowl, self.cup = _bowl(), Item.box("cup", 0.08, 0.08, 0.06, mass=0.2)
        self.container = Container(id="box", dims=(0.20, 0.20, 0.101), gravity=True)
        self.result = pack_naive(self.container, [self.bowl, self.cup])
        self.assertEqual({p.item_id for p in self.result.placements}, {"bowl", "cup"},
                         self.result.unpacked)
        self.assertIsNotNone(next(p for p in self.result.placements
                                  if p.item_id == "cup").nested_in)

    def test_the_hosts_slack_excludes_what_its_guest_fills(self):
        rep = gap_report(self.result, [self.bowl, self.cup], verbose=False)
        slack = {g["id"]: g["volume"] for g in rep["gap_items"] if g["kind"] == "bbox slack"}
        self.assertAlmostEqual(slack["bowl"], self.WELL_AIR, places=9)
        self.assertNotIn("cup", slack)   # a solid box reserves exactly its own volume

    def test_the_gap_it_reports_no_longer_exceeds_the_waste_it_explains(self):
        rep = gap_report(self.result, [self.bowl, self.cup], verbose=False)
        self.assertAlmostEqual(rep["wasted_volume"], self.WASTED, places=9)
        total = sum(g["volume"] for g in rep["gap_items"])
        self.assertLessEqual(total, rep["wasted_volume"] + 1e-12)
        # and it is not merely under: the well air plus the rim headroom IS the waste, so
        # the attribution is now exact rather than 0.0009 against a 0.000556 total.
        self.assertAlmostEqual(total + self.HEADROOM, self.WASTED, places=9)

    def test_the_waste_floor_is_stated_on_true_volume_so_the_nest_cannot_inflate_it(self):
        wb = waste_bounds(self.container, [self.bowl, self.cup])
        self.assertAlmostEqual(wb["usable_volume"], self.USABLE, places=9)
        # both items fit and no mass limit, so the ceiling is their whole true volume --
        # 0.003484, NOT 0.004384 (the boxes) and NOT 0.003100 (the boxes de-duplicated by
        # the nest, which would credit the cup with nothing).
        self.assertAlmostEqual(wb["max_packable_volume"], 0.003484, places=9)
        self.assertAlmostEqual(wb["wasted_volume_lower_bound"], self.WASTED, places=9)
        self.assertLessEqual(wb["wasted_volume_lower_bound"],
                             self.USABLE - self.result.metrics["packed_volume"] + 1e-12)

    def test_the_solver_metric_and_the_app_metric_are_different_quantities_on_purpose(self):
        # volume_utilization = true solid volume / usable = 0.003484 / 0.00404 = 86.2376%
        self.assertAlmostEqual(self.result.metrics["volume_utilization"],
                               0.003484 / self.USABLE, places=9)
        self.assertAlmostEqual(self.result.metrics["volume_utilization"], 0.8623762376237624,
                               places=9)
        # the app's fill is the union of the BOXES: the cup's box lies wholly inside the
        # bowl's, so the union is the bowl's box alone, 0.004 / 0.00404 = 99.0099%.
        self.assertAlmostEqual(packed_bbox_volume(self.result.placements), 0.004, places=9)
        self.assertAlmostEqual(packed_bbox_volume(self.result.placements) / self.USABLE,
                               0.9900990099009901, places=9)


if __name__ == "__main__":
    unittest.main()
