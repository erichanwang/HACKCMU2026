"""`server/planner.py`: physics pre-pass -> several solver runs -> physics picks the plan."""
import importlib
import math
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import planner  # noqa: E402


def scan(item_id, w, h, d, **over):
    cell = 0.05
    rows, cols = math.ceil(w / cell), math.ceil(d / cell)
    return {"id": item_id, "dimensions": [w, h, d], "cellSize": cell, "heights": [[h] * cols for _ in range(rows)],
            "rigidity": "rigid", "compressibility": 1.0, "mass": 0.5, "keepUpright": False, "label": item_id} | over


def cand(valid=True, violations=0, packed=3, score=1.0, util=0.5, com=0.01):
    return {"validation": {"valid": valid, "violations": [{}] * violations, "score": score},
            "solver": {"metrics": {"items_packed": packed, "volume_utilization": util, "com_lateral_offset": com}}}


class TestRanking(unittest.TestCase):
    def test_physics_verdict_outranks_solver_metrics(self):
        invalid_but_full = cand(valid=False, violations=1, packed=5, util=0.9)
        valid_but_sparse = cand(valid=True, packed=3, util=0.3)
        self.assertGreater(planner.rank(valid_but_sparse), planner.rank(invalid_but_full))

    def test_then_more_packed_then_score_then_utilisation(self):
        self.assertGreater(planner.rank(cand(packed=4)), planner.rank(cand(packed=3, score=1.0, util=0.9)))
        self.assertGreater(planner.rank(cand(score=1.0)), planner.rank(cand(score=0.8, util=0.9)))
        self.assertGreater(planner.rank(cand(util=0.6)), planner.rank(cand(util=0.5)))


class TestPlan(unittest.TestCase):
    def test_plan_runs_physics_first_and_picks_the_best_candidate(self):
        planner.TIME_BUDGET_S = 0.3
        suitcase = {"_id": "s1", "name": "test", "dimensions": [0.4, 0.2, 0.3]}
        items = [scan("shirts", 0.3, 0.1, 0.2, rigidity="soft", compressibility=2.0),
                 scan("bottle", 0.07, 0.18, 0.07, keepUpright=True),
                 scan("book", 0.15, 0.04, 0.2)]
        doc = planner.plan(suitcase, items)
        self.assertEqual(doc["chosen"]["strategy"], doc["solver"]["strategy"])
        self.assertEqual(len(doc["alternatives"]), len(planner.CANDIDATES))
        self.assertEqual(doc["alternatives"][0]["strategy"], doc["chosen"]["strategy"])
        self.assertTrue(doc["validation"]["valid"], doc["validation"]["violations"])
        self.assertEqual(doc["solver"]["metrics"]["items_packed"], 3)
        sizes = {p["itemId"]: p["size"] for p in doc["plan"]["placements"]}
        self.assertAlmostEqual(min(sizes["shirts"].values()), 0.05)  # physics halved the shirts
        self.assertAlmostEqual(sizes["bottle"]["y"], 0.18)          # and kept the bottle upright

    def test_plan_also_stores_the_runner_up_s_full_placements(self):
        planner.TIME_BUDGET_S = 0.3
        suitcase = {"_id": "s1", "name": "test", "dimensions": [0.4, 0.2, 0.3]}
        items = [scan("shirts", 0.3, 0.1, 0.2, rigidity="soft", compressibility=2.0),
                 scan("bottle", 0.07, 0.18, 0.07, keepUpright=True),
                 scan("book", 0.15, 0.04, 0.2)]
        doc = planner.plan(suitcase, items)
        runner_up = doc["runnerUp"]
        self.assertEqual(runner_up["chosen"]["strategy"], doc["alternatives"][1]["strategy"])
        self.assertEqual(runner_up["chosen"]["seed"], doc["alternatives"][1]["seed"])
        self.assertTrue(runner_up["plan"]["placements"])
        # the winner's own document shape is unchanged
        self.assertTrue(doc["plan"]["placements"])
        self.assertEqual(doc["chosen"]["strategy"], doc["alternatives"][0]["strategy"])


class TestUnpacked(unittest.TestCase):
    def test_unpacked_items_are_reported_outside_the_plan(self):
        planner.TIME_BUDGET_S = 0.2
        suitcase = {"_id": "s1", "name": "test", "dimensions": [0.4, 0.2, 0.3]}
        items = [scan("book", 0.15, 0.04, 0.2),
                 scan("giant", 0.5, 0.5, 0.5, label="Giant Bag")]  # bigger than the suitcase in every dimension
        doc = planner.plan(suitcase, items)
        self.assertEqual(doc["unpacked"], [{"itemId": "giant", "label": "Giant Bag"}])
        self.assertNotIn("unpacked", doc["plan"])  # the PackingPlan contract is untouched

    def test_unpacked_is_empty_when_everything_fits(self):
        planner.TIME_BUDGET_S = 0.2
        suitcase = {"_id": "s1", "name": "test", "dimensions": [0.4, 0.2, 0.3]}
        doc = planner.plan(suitcase, [scan("book", 0.15, 0.04, 0.2)])
        self.assertEqual(doc["unpacked"], [])


class TestTimeBudgetEnv(unittest.TestCase):
    def test_env_var_sets_time_budget(self):
        with mock.patch.dict(os.environ, {"PLAN_TIME_BUDGET_S": "0.2"}):
            importlib.reload(planner)
            self.assertEqual(planner.TIME_BUDGET_S, 0.2)
        importlib.reload(planner)  # restore the default for the rest of the suite


class TestFixedIterations(unittest.TestCase):
    """`PLAN_ITERATIONS` makes a plan reproducible: same scan in, same plan out.

    The default wall-clock budget cannot promise that -- the search stops when the clock says
    so, and a loaded laptop reaches a different place in the same second, so the same bag can
    yield a different layout between the rehearsal and the demo.
    """

    def test_env_var_switches_to_fixed_work(self):
        with mock.patch.dict(os.environ, {"PLAN_ITERATIONS": "25"}):
            importlib.reload(planner)
            self.assertEqual(planner.PLAN_ITERATIONS, 25)
        importlib.reload(planner)
        self.assertEqual(planner.PLAN_ITERATIONS, 0, "default must stay on the wall-clock budget")

    def _configs_used(self, env):
        """The OptimizerConfig each optimised candidate is actually solved with."""
        suitcase = {"_id": "s1", "name": "test", "dimensions": [0.4, 0.2, 0.3]}
        items = [scan("shirts", 0.3, 0.1, 0.2, rigidity="soft", compressibility=2.0),
                 scan("bottle", 0.07, 0.18, 0.07, keepUpright=True),
                 scan("book", 0.15, 0.04, 0.2)]
        seen = []
        with mock.patch.dict(os.environ, env):
            importlib.reload(planner)
            real = planner.pack_optimized

            def spy(container, packer_items, config=None, **kw):
                seen.append(config)
                return real(container, packer_items, config=config, **kw)

            with mock.patch.object(planner, "pack_optimized", spy):
                doc = planner.plan(suitcase, items)
        importlib.reload(planner)
        return seen, doc

    def test_fixed_iterations_bounds_the_work_not_the_clock(self):
        """The mechanism, asserted directly.

        Two runs comparing equal is necessary but NOT sufficient: on a small scenario the
        annealing makes no improvement at all (`stats.sa_improvements == 0`), so the wall-clock
        default also repeats itself and an equality-only test passes without proving anything.
        What actually delivers reproducibility is the config the solver is handed, so check that.
        """
        seen, _ = self._configs_used({"PLAN_ITERATIONS": "25"})
        self.assertTrue(seen, "no optimised candidate was solved")
        for config in seen:
            self.assertEqual(config.time_budget_s, 0.0, "the clock must not bound a fixed-work run")
            self.assertEqual(config.max_iterations, 25)
        self.assertEqual(len({c.seed for c in seen}), len(seen), "each candidate keeps its own seed")

    def test_default_still_bounds_the_clock(self):
        """Unset, the wall-clock budget stays -- for an unknown bag, bounding the wait a judge
        sits through is safer than bounding the work."""
        seen, _ = self._configs_used({"PLAN_ITERATIONS": "0"})
        for config in seen:
            self.assertGreater(config.time_budget_s, 0.0)
            self.assertIsNone(config.max_iterations)

    def test_two_runs_at_fixed_iterations_agree(self):
        """Weaker regression guard: see the docstring above for why this alone proves little."""
        _, first = self._configs_used({"PLAN_ITERATIONS": "10"})
        _, second = self._configs_used({"PLAN_ITERATIONS": "10"})
        self.assertEqual(first["plan"], second["plan"])


class TestLock(unittest.TestCase):
    def test_plan_holds_the_lock_while_solving(self):
        real_pack_naive = planner.pack_naive
        locked_during_solve = []

        def wrapped(*args, **kwargs):
            locked_during_solve.append(planner._lock.locked())
            return real_pack_naive(*args, **kwargs)

        planner.TIME_BUDGET_S = 0.1
        suitcase = {"_id": "s1", "name": "test", "dimensions": [0.4, 0.2, 0.3]}
        items = [scan("book", 0.15, 0.04, 0.2)]
        with mock.patch.object(planner, "pack_naive", side_effect=wrapped):
            planner.plan(suitcase, items)
        self.assertTrue(locked_during_solve)
        self.assertTrue(all(locked_during_solve))


if __name__ == "__main__":
    unittest.main()
