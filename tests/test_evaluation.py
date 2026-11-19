"""Step 3 tests: comparisons are declared in the strategy file, history is split in advance, fixed scorecard."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import backtest  # noqa: E402
import engine  # noqa: E402
import golden_tools  # noqa: E402

SCORECARD_KEYS = {"trades", "win_rate", "avg_per_1000", "total_per_1000", "worst_per_1000", "max_losing_streak",
                  "max_drawdown_per_1000", "by_condition", "hold_sol"}


def strategy_with(variants_toml: str) -> Path:
    """A copy of the real strategy file whose [[variants]] list is replaced."""
    text = engine.DEFAULT_STRATEGY.read_text(encoding="utf-8")
    text = text[:text.index("[[variants]]")] + variants_toml
    d = Path(tempfile.mkdtemp())
    p = d / "test_strategy.toml"
    p.write_text(text, encoding="utf-8")
    return p


def run_slice(strategy_path=None) -> dict:
    return backtest.run_all(strategy_path, quiet=True, history_path=golden_tools.BACKTEST_HISTORY,
                            chains_dir=golden_tools.NO_CHAINS)


class DeclaredInTheStrategyFile(unittest.TestCase):
    def test_backtest_has_no_variants_of_its_own(self):
        src = (ROOT / "backtest.py").read_text(encoding="utf-8")
        self.assertNotIn("VARIANTS", src)
        self.assertNotIn("stop_multiple", src)              # no exit rule is set in the backtest code

    def test_report_lists_exactly_the_declared_variants(self):
        p = strategy_with('[[variants]]\nname = "A"\n\n[[variants]]\nname = "B"\nexits = { stop_multiple = 2.0 }\n')
        try:
            out = run_slice(p)
        finally:
            shutil.rmtree(p.parent, ignore_errors=True)
        self.assertEqual([v["name"] for v in out["variants"]], ["A", "B"])
        self.assertEqual(out["variants"][1]["settings"], {"exits": {"stop_multiple": 2.0}})

    def test_real_file_variants_all_reported(self):
        st = engine.load_strategy()
        out = run_slice()
        self.assertEqual([v["name"] for v in out["variants"]], [v["name"] for v in st["variants"]])

    def test_typos_are_refused(self):
        st = engine.load_strategy()
        for bad in ({"name": "x", "exits": {"stop_multipel": 2}},      # misspelt exit rule
                    {"name": "x", "stop_multiple": 2},                 # rule outside exits = { }
                    {"name": ""}):
            with self.assertRaises(ValueError):
                engine.variants(dict(st, variants=[bad]))
        with self.assertRaises(ValueError):
            engine.variants(dict(st, variants=[{"name": "a"}, {"name": "a"}]))
        with self.assertRaises(ValueError):
            engine.variants(dict(st, variants=[]))

    def test_variant_overrides(self):
        st = engine.load_strategy()
        v = engine.variants(dict(st, variants=[{"name": "x", "require_verdict": "", "one_at_a_time": False,
                                                "exits": {"time_exit_dte": 0}}]))[0]
        self.assertIsNone(v["strategy"]["entry"]["require_verdict"])
        self.assertFalse(v["one_at_a_time"])
        self.assertEqual(v["strategy"]["exits"]["time_exit_dte"], 0)
        self.assertEqual(v["strategy"]["exits"]["take_profit_pct"], st["exits"]["take_profit_pct"])
        self.assertEqual(st["exits"]["time_exit_dte"], 7)                # the original is untouched

    def test_daily_scan_rules_unchanged_by_variants(self):
        st = engine.load_strategy()
        view = engine.with_strategy({"risk": {}}, st)
        self.assertEqual(view["exits"], st["exits"])
        self.assertEqual(st["exits"]["stop_multiple"], 1.5)


class SplitHistory(unittest.TestCase):
    def setUp(self):
        self.out = run_slice()

    def test_windows_side_by_side_for_every_variant(self):
        self.assertEqual(self.out["windows"], {"in_sample": ["2024-06-01", "2025-06-30"],
                                               "held_back": ["2025-07-01", "2025-12-31"]})   # "" = end of data
        for v in self.out["variants"]:
            self.assertEqual(set(v["scorecard"]), {"in_sample", "held_back"})
            for sc in v["scorecard"].values():
                self.assertTrue(SCORECARD_KEYS <= set(sc) or sc["trades"] == 0)

    def test_trades_belong_to_their_entry_window(self):
        w = self.out["windows"]
        for v in self.out["variants"]:
            inside = [t for t in v["trades"] if w["in_sample"][0] <= t["date"] <= w["held_back"][1]]
            n = v["scorecard"]["in_sample"]["trades"] + v["scorecard"]["held_back"]["trades"]
            self.assertEqual(n, len(inside))
            is_n = sum(t["date"] <= w["in_sample"][1] for t in inside)
            self.assertEqual(is_n, v["scorecard"]["in_sample"]["trades"])

    def test_report_text(self):
        text = backtest.report(self.out)
        self.assertIn("IN-SAMPLE 2024-06-01 to 2025-06-30", text)
        self.assertIn("HELD BACK 2025-07-01 to 2025-12-31", text)
        for v in self.out["variants"]:
            self.assertIn(v["name"][:38], text)
        self.assertIn("Holding SOL instead", text)


class ScorecardPieces(unittest.TestCase):
    def test_losing_streak(self):
        self.assertEqual(backtest.losing_streak([5, -1, -2, 3, -1, -1, -1, 0, -4]), 3)
        self.assertEqual(backtest.losing_streak([]), 0)

    def test_by_condition(self):
        t = lambda v, s0, s1, p: {"verdict": v, "S": s0, "S_exit": s1, "pnl_per_1000": p}
        out = backtest.by_condition([t("RICH", 100, 110, 50), t("RICH", 100, 90, -200), t("RICH", 100, 95, 30),
                                     t("CHEAP", 100, 120, 10)])
        self.assertEqual(out["RICH, SOL down"], {"trades": 2, "wins": 1, "total_per_1000": -170.0,
                                                 "avg_per_1000": -85.0})
        self.assertEqual(out["CHEAP, SOL up"]["trades"], 1)
        self.assertEqual(out["RICH, SOL up"]["wins"], 1)

    def test_hold_sol(self):
        days = [{"date": "2025-01-01", "S": 100.0}, {"date": "2025-02-01", "S": 80.0},
                {"date": "2025-03-01", "S": 150.0}]
        self.assertEqual(backtest.hold_sol(days, "2025-01-01", "2025-02-28")["per_1000"], -200.0)
        self.assertEqual(backtest.hold_sol(days, "2025-01-01", "2025-12-31")["per_1000"], 500.0)


if __name__ == "__main__":
    unittest.main()
